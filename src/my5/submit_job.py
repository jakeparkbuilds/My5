"""
Job submission: build a job record in DynamoDB and enqueue a pointer in SQS.

submit_job() is the public entry point. It:
  0. (Optional) Check cache: if seed is set and a SimCache is provided, a hit
     returns SubmitResult(cache_hit=True, cached_result=...) immediately —
     no job record written, no SQS message, no worker, no WebSocket.
  1. Generate a UUID4 job_id.
  2. Write a QUEUED job record to my5-sim-jobs (DynamoDB).
  3. Send {"job_id": job_id} to my5-jobs (SQS).
  4. Return SubmitResult(job_id=job_id, cache_hit=False).

The SQS message body is intentionally minimal (pointer only). All job parameters
live in DynamoDB, which is the single source of truth. This separates the queue
schema from the job schema — they can evolve independently, and in-flight messages
from old deployments remain valid during rolling updates.
"""
from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from my5.job_store import JobStore
from my5.logging_utils import ENV as _ENV, emit_emf, log_job_event
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, SimResult

if TYPE_CHECKING:
    from my5.cache import SimCache


@dataclass
class SubmitResult:
    """
    Return value of submit_job().

    cache_hit=True  → cached_result is populated; job_id is None (no job was created).
    cache_hit=False → job_id is a UUID4; cached_result is None; job is queued normally.
    """
    job_id: str | None
    cache_hit: bool
    cached_result: SimResult | None = None

# ── Default league averages (52-game 2024-25 NBA slice) ──────────────────────
#
# These are the empirical values from our validated aggregation. The caller can
# override by passing `league=...` to submit_job. When the full season is loaded,
# re-run run_aggregation.py and update these constants — the stored values in each
# job record remain the ground truth for replay even after the constants change.
_DEFAULT_LEAGUE = LeagueAverages(
    usage_rate=0.19,
    rim_fg_pct=0.6160,
    mid_fg_pct=0.4389,
    fg3_pct=0.3795,
    tov_rate=0.1119,
    ft_rate=0.0205,
    ft_pct=0.7816,
    oreb_rate=0.0433,
    shot_rim_rate=0.363,
    shot_mid_rate=0.235,
    shot_3p_rate=0.402,
    opp_rim_fg_pct=0.6160,
    opp_mid_fg_pct=0.4389,
    opp_3p_fg_pct=0.3795,
    forced_to_rate=0.1262,
    dreb_rate=0.7214,
)

_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _serialize_league(league: LeagueAverages) -> dict[str, Any]:
    """
    Convert LeagueAverages to a DynamoDB-safe dict.

    boto3 resource API rejects Python floats — all must be Decimal(str(x)).
    Stored in the job record so the worker can reconstruct the exact LeagueAverages
    object used at submit time, making each job independently reproducible.
    """
    return {k: Decimal(str(v)) for k, v in dataclasses.asdict(league).items()}


def submit_job(
    team_a_key: str,
    team_a_player_ids: list[int],
    team_b_key: str,
    team_b_player_ids: list[int],
    *,
    seed: int | None = None,
    league: LeagueAverages | None = None,
    job_store: JobStore | None = None,
    queue_client: QueueClient | None = None,
    cache: "SimCache | None" = None,
) -> SubmitResult:
    """
    Submit a simulation job and return a SubmitResult.

    team_a_key / team_b_key:
      The DynamoDB lineup_key string ("team_id#id0#id1#id2#id3#id4") for lineup
      defensive metrics. Use "hypothetical" when no historical metrics exist — the
      simulator will use league-average defense automatically (n=0 in shrinkage).

    team_a_player_ids / team_b_player_ids:
      List of 5 ESPN athlete_id integers. Must exist in my5-player-params for the
      simulation to proceed; missing IDs cause the job to FAIL with "invalid_lineup".

    seed:
      RNG seed for deterministic replay. None = system entropy (non-reproducible).
      Recommended: pass a seed for debugging; omit for production simulations.
      Cache is only consulted when seed is provided (non-deterministic runs can't cache).

    league:
      League-wide rate baselines. Defaults to the 52-game aggregation constants
      above. Pass a custom LeagueAverages when testing with different baselines.

    job_store / queue_client / cache:
      Injectable for tests. Default to the real DynamoDB / SQS / cache clients.

    Returns: SubmitResult. On cache hit: job_id=None, cache_hit=True, cached_result populated.
             On cache miss: job_id=<uuid>, cache_hit=False, cached_result=None.
    """
    if len(team_a_player_ids) != 5 or len(team_b_player_ids) != 5:
        raise ValueError("Each team must have exactly 5 player IDs.")

    if league is None:
        league = _DEFAULT_LEAGUE

    # ── Cache check (submit-time, before any DynamoDB write or SQS enqueue) ──
    # Only when seed is provided: non-deterministic runs produce different results
    # each time and must never be cached.
    if cache is not None and seed is not None:
        from my5.cache import make_cache_key
        _t0 = time.perf_counter()
        cache_key = make_cache_key(team_a_key, team_b_key, seed)
        cached = cache.get(cache_key)
        if cached is not None:
            latency_ms = int((time.perf_counter() - _t0) * 1000)
            log_job_event("cache_hit", "submit_cache_hit", latency_ms=latency_ms)
            emit_emf(
                metrics={"job_latency_ms": (latency_ms, "Milliseconds"), "cache_hit_count": (1, "Count")},
                dimensions={"env": _ENV, "cache_status": "hit"},
            )
            return SubmitResult(job_id=None, cache_hit=True, cached_result=cached)

    if job_store is None:
        job_store = JobStore()
    if queue_client is None:
        queue_client = QueueClient()

    job_id = str(uuid.uuid4())
    now = _now_iso()

    # ── Build the job record ──────────────────────────────────────────────────
    item: dict[str, Any] = {
        "job_id":             job_id,
        "team_a_key":         team_a_key,
        "team_b_key":         team_b_key,
        "team_a_player_ids":  [int(x) for x in team_a_player_ids],
        "team_b_player_ids":  [int(x) for x in team_b_player_ids],
        "league":             _serialize_league(league),
        "status":             "queued",
        "attempt_count":      0,
        "queued_at":          now,
        "ttl":                int(time.time()) + _TTL_SECONDS,
    }
    if seed is not None:
        item["seed"] = int(seed)

    # ── Write job record first, then enqueue ──────────────────────────────────
    # Order matters: if the enqueue call fails, the job record exists but has no
    # SQS message. That's a stranded job (stuck at QUEUED forever), which is
    # visible and recoverable by re-enqueueing. The reverse order (enqueue then
    # write) would deliver a message whose job_id doesn't exist yet — a race
    # condition where the worker reads QUEUED before the record is committed.
    job_store.put_job(item)
    queue_client.enqueue(job_id)

    return SubmitResult(job_id=job_id, cache_hit=False)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
