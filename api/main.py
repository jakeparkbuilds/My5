"""
My5 HTTP API — FastAPI layer wrapping the existing Python backend.

Three endpoints:
  GET  /api/players            → full player list (loaded once at startup)
  POST /api/simulate           → submit_job(), returns hit/miss + result
  GET  /api/jobs/{job_id}      → job status (reconnect fallback + result source of truth)

Player data strategy:
  At startup we scan my5-player-params once (small table, ~400 rows) and hold
  the result in memory. The GET /api/players endpoint serves from that cache
  with zero per-request DynamoDB calls. The client downloads the full list once
  and filters locally — no per-query scans, ever.

Deploy locally:
  MY5_ENV=local uvicorn api.main:app --reload --port 8001

Deploy on AWS via Lambda + Mangum (scale-to-zero, $0 idle):
  handler = Mangum(app)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

# Make the src/ directory importable when running as `uvicorn api.main:app`
# from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.config import CORS_ORIGINS
from api.models import (
    JobStatusResponse,
    PlayerOut,
    SimResultOut,
    SimulateRequest,
    SimulateResponse,
)
from my5.cache import SimCache
from my5.config import make_dynamo_resource
from my5.job_store import JobStore
from my5.queue_client import QueueClient
from my5.submit_job import _DEFAULT_LEAGUE, submit_job

log = logging.getLogger("my5.api")
logging.basicConfig(level=logging.INFO)

# ── In-memory player cache ─────────────────────────────────────────────────────

_PLAYERS_META_PATH = Path(__file__).parent / "data" / "players_meta.json"

# Populated at startup — athlete_id → PlayerOut
_player_cache: dict[int, PlayerOut] = {}


def _dec(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_player_cache() -> None:
    """
    Scan my5-player-params once and merge with players_meta.json.

    Only players with simulation params (i.e., rows in my5-player-params) are
    included — this guarantees every player the frontend can pick will actually
    run through the simulator without a KeyError.
    """
    log.info("Loading player metadata from disk...")
    with open(_PLAYERS_META_PATH) as f:
        meta_list: list[dict] = json.load(f)
    meta_by_id = {m["athlete_id"]: m for m in meta_list}

    log.info("Scanning my5-player-params (one-time startup scan)...")
    dynamo = make_dynamo_resource()
    table = dynamo.Table("my5-player-params")

    # Full table scan — the table is small (~400 rows, ~50 KB).
    # Never called again after startup; results live in _player_cache.
    params_by_id: dict[int, dict] = {}
    resp = table.scan()
    for item in resp.get("Items", []):
        aid = int(item["athlete_id"])
        params_by_id[aid] = item
    # Handle DynamoDB pagination (shouldn't occur at this scale, but be correct)
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        for item in resp.get("Items", []):
            aid = int(item["athlete_id"])
            params_by_id[aid] = item

    log.info("Found %d players with simulation params", len(params_by_id))

    for aid, params in params_by_id.items():
        meta = meta_by_id.get(aid, {})
        _player_cache[aid] = PlayerOut(
            athlete_id=aid,
            display_name=meta.get("display_name", f"Player {aid}"),
            short_name=meta.get("short_name", f"#{aid}"),
            team_abbr=meta.get("team_abbr", ""),
            team_name=meta.get("team_name", ""),
            team_id=meta.get("team_id"),
            headshot_href=meta.get("headshot_href", ""),
            usage_rate=_dec(params.get("usage_rate")),
            fg3_pct=_dec(params.get("fg3_pct")),
            rim_fg_pct=_dec(params.get("rim_fg_pct")),
            mid_fg_pct=_dec(params.get("mid_fg_pct")),
            tov_rate=_dec(params.get("tov_rate")),
            ft_pct=_dec(params.get("ft_pct")),
        )

    log.info("Player cache ready: %d players", len(_player_cache))


# ── Shared backend clients (created once, reused across requests) ──────────────

_job_store: JobStore | None = None
_queue_client: QueueClient | None = None
_sim_cache: SimCache | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _job_store, _queue_client, _sim_cache
    # Guards against re-initialization on warm Lambda containers.
    # Mangum 0.17+ re-runs lifespan events on every Lambda invocation;
    # module-level globals persist across warm invocations, so these
    # checks ensure the DynamoDB scan and client setup run only once.
    if not _player_cache:
        _load_player_cache()
    if _job_store is None:
        _job_store = JobStore()
    if _queue_client is None:
        _queue_client = QueueClient()
    if _sim_cache is None:
        _sim_cache = SimCache()
    yield
    # Nothing to clean up — boto3 clients have no close().


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="My5 API", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/api/players", response_model=list[PlayerOut])
async def list_players() -> list[PlayerOut]:
    """
    Return every player who has simulation params in my5-player-params.

    Served from in-memory cache (populated at startup). Zero DynamoDB calls
    per request. The frontend downloads this once and filters client-side.
    """
    return sorted(_player_cache.values(), key=lambda p: p.display_name)


@app.post("/api/simulate", response_model=SimulateResponse)
async def simulate(req: SimulateRequest) -> SimulateResponse:
    """
    Submit a matchup simulation.

    team_a / team_b player IDs must exist in my5-player-params.
    lineup_key is always "hypothetical" — the sandbox is for cross-team/
    cross-era matchups, so historical lineup defensive metrics won't exist.
    The simulator falls back to league-average defense via shrinkage (n=0).

    Returns immediately on a cache hit (no job created, no SQS message).
    Returns a job_id on a miss; frontend connects to the WebSocket with it.
    """
    # Validate every player exists in our data
    all_ids = set(req.team_a_player_ids) | set(req.team_b_player_ids)
    missing = all_ids - set(_player_cache.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Players not in my5-player-params: {sorted(missing)}",
        )

    result = submit_job(
        team_a_key="hypothetical",
        team_a_player_ids=req.team_a_player_ids,
        team_b_key="hypothetical",
        team_b_player_ids=req.team_b_player_ids,
        seed=req.seed,
        league=_DEFAULT_LEAGUE,
        job_store=_job_store,
        queue_client=_queue_client,
        cache=_sim_cache,
    )

    cached_out: SimResultOut | None = None
    if result.cache_hit and result.cached_result is not None:
        r = result.cached_result
        cached_out = SimResultOut(
            mean_margin=r.mean_margin,
            ci_half_width=r.ci_half_width,
            n_sims=r.n_sims,
            equiv_net_rating=r.equiv_net_rating,
            converged=r.converged,
        )

    return SimulateResponse(
        job_id=result.job_id,
        cache_hit=result.cache_hit,
        cached_result=cached_out,
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    """
    Read a job record directly from DynamoDB.

    Used by the frontend as the source of truth on WebSocket reconnect
    (never trust the socket as the store) and as a 60-second timeout fallback.
    """
    try:
        record = _job_store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    status = record.get("status", "queued")
    result_out: SimResultOut | None = None
    if status == "done":
        r = record.get("result", {})
        result_out = SimResultOut(
            mean_margin=float(r.get("mean_margin", 0)),
            ci_half_width=float(r.get("ci_half_width", 0)),
            n_sims=int(r.get("n_sims", 0)),
            equiv_net_rating=float(r.get("equiv_net_rating", 0)),
            converged=bool(r.get("converged", False)),
        )

    return JobStatusResponse(
        job_id=job_id,
        status=status,
        result=result_out,
        error_type=record.get("error_type"),
        error_message=record.get("error_message"),
        sims_done=int(record.get("progress_sims") or 0),
        ci_half=float(record.get("progress_ci") or 0.0),
    )


# ── Lambda entry point ─────────────────────────────────────────────────────────
# Mangum wraps the ASGI app so API Gateway HTTP API can invoke it.
# Locally, uvicorn bypasses this — Mangum is only used by Lambda.
try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    handler = None  # Mangum not installed locally — that's fine
