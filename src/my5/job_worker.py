"""
Job worker: core handler + local polling loop + DLQ monitor.

The architecture has a hard seam between the invocation shell (HOW a job is
triggered) and the core handler (WHAT happens to the job). The core handler is
identical on local polling and Lambda:

  LOCAL:                         LAMBDA (AWS):
  polling_loop()                 lambda_handler(event, ctx)
    └─ receive SQS message         └─ iterate event["Records"]
         └─ handle_job()               └─ handle_job()
              ↑                              ↑
              same function, same logic ────┘

To port to Lambda: write a lambda_handler that iterates event["Records"] and
calls handle_job. The core logic here never changes.

State machine (enforced in handle_job):
  QUEUED → RUNNING: worker claims job, increments attempt_count
  RUNNING → DONE:   engine succeeds, result written, message deleted
  RUNNING → FAILED: invalid lineup (delete msg, no retry) or DLQ path (monitor)
  RUNNING → QUEUED: implicit — worker crashes, VisibilityTimeout=60s expires,
                    message reappears. No code here; SQS handles it automatically.
  DONE → skip:      duplicate delivery — delete msg, return immediately (idempotent)
"""
from __future__ import annotations

import dataclasses
import datetime
import os
import socket
import time
from decimal import Decimal
from typing import Any, Callable

from my5.config import DLQ_URL, make_dynamo_resource
from my5.job_store import JobStore, LineupNotFoundError
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, simulate

# Worker identity embedded in the job record for diagnosis.
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── DynamoDB item helpers ─────────────────────────────────────────────────────


def _dec_to_float(obj: Any) -> Any:
    """Convert Decimal values from boto3 resource API to float for the simulator."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _dec_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec_to_float(x) for x in obj]
    return obj


def _default_fetch_lineup(
    player_ids: list[int],
    lineup_key: str,
    *,
    dynamo_resource: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Fetch 5 player-params dicts and one lineup-metrics dict (or None) from DynamoDB.

    player_ids: 5 athlete_id integers → looked up in my5-player-params
    lineup_key: lineup DynamoDB key   → looked up in my5-lineup-metrics
                "hypothetical"        → returns None for lineup (league-avg defense)

    Raises LineupNotFoundError if any player is missing or the lineup key is absent.

    Note: batch_get_item does not guarantee item order. The simulator is order-
    agnostic (selects ball-handler by usage_rate weights), so order doesn't matter.
    Unprocessed keys (DynamoDB throttling) are ignored — acceptable at local scale.
    """
    # Fetch all 5 players via batch_get_item (one round-trip).
    response = dynamo_resource.batch_get_item(
        RequestItems={
            "my5-player-params": {
                "Keys": [{"athlete_id": Decimal(str(pid))} for pid in player_ids]
            }
        }
    )
    players_raw = response["Responses"].get("my5-player-params", [])

    if len(players_raw) != len(player_ids):
        found_ids = {int(p["athlete_id"]) for p in players_raw}
        missing = [pid for pid in player_ids if pid not in found_ids]
        raise LineupNotFoundError(
            f"Players not found in my5-player-params: {missing}"
        )

    players = [_dec_to_float(p) for p in players_raw]

    # Hypothetical lineup → no defensive history → simulator uses league-average defense.
    if lineup_key == "hypothetical":
        return players, None

    lineup_table = dynamo_resource.Table("my5-lineup-metrics")
    resp = lineup_table.get_item(Key={"lineup_key": lineup_key})
    if "Item" not in resp:
        raise LineupNotFoundError(
            f"Lineup key {lineup_key!r} not found in my5-lineup-metrics"
        )
    lineup = _dec_to_float(resp["Item"])
    return players, lineup


def _league_from_dict(d: dict[str, Any]) -> LeagueAverages:
    """Reconstruct a LeagueAverages dataclass from the dict stored in a job record."""
    fields = {f.name for f in dataclasses.fields(LeagueAverages)}
    return LeagueAverages(**{k: float(v) for k, v in d.items() if k in fields})


# ── Core handler (portable: local polling and Lambda call the same function) ──


def handle_job(
    job_id: str,
    receipt_handle: str,
    *,
    queue_client: QueueClient,
    job_store: JobStore,
    fetch_lineup: Callable | None = None,
    dynamo_resource: Any = None,
) -> str:
    """
    Process one simulation job end-to-end.

    Returns the terminal status: "done", "failed", or "skipped" (idempotent duplicate).

    Failure modes and how each is handled:
      - Already done (duplicate delivery): delete msg, return "skipped". Idempotent
        because the sealed engine is deterministic — same seed → same result.
      - Invalid lineup / missing player: set FAILED, delete msg (no retry — the
        data won't appear on its own).
      - Unexpected exception before delete: re-raise so the caller does NOT delete
        the message. VisibilityTimeout=60s then expires and SQS retries automatically.
        After 3 retries, SQS routes to the DLQ and the DLQ monitor sets FAILED.

    fetch_lineup: injectable for tests. Default hits DynamoDB via _default_fetch_lineup.
    dynamo_resource: the boto3 DynamoDB resource; created lazily if not provided.
    """
    # ── 1. Idempotency check ──────────────────────────────────────────────────
    job = job_store.get_job(job_id)
    if job["status"] == "done":
        queue_client.delete(receipt_handle)
        return "skipped"

    # ── 2. Claim the job (QUEUED → RUNNING) ──────────────────────────────────
    job_store.update_status(
        job_id, "running",
        increment_attempt=True,
        extra={"started_at": _now_iso(), "worker_id": _WORKER_ID},
    )

    # ── 3. Fetch lineup data ──────────────────────────────────────────────────
    if fetch_lineup is None:
        if dynamo_resource is None:
            dynamo_resource = make_dynamo_resource()

        def fetch_lineup(player_ids: list[int], lineup_key: str) -> tuple:
            return _default_fetch_lineup(
                player_ids, lineup_key, dynamo_resource=dynamo_resource
            )

    try:
        team_a_players, team_a_lineup = fetch_lineup(
            [int(x) for x in job["team_a_player_ids"]],
            job["team_a_key"],
        )
        team_b_players, team_b_lineup = fetch_lineup(
            [int(x) for x in job["team_b_player_ids"]],
            job["team_b_key"],
        )
    except LineupNotFoundError as exc:
        # Invalid lineup: fail fast — retrying won't help, the data won't appear.
        job_store.fail_job(
            job_id,
            error_type="invalid_lineup",
            error_message=str(exc),
            completed_at=_now_iso(),
        )
        queue_client.delete(receipt_handle)
        return "failed"

    # ── 4. Run the sealed engine ──────────────────────────────────────────────
    # The engine is deterministic: same seed → same SimResult. If this job is
    # delivered twice (rare VisibilityTimeout race), both workers compute the
    # same result — last write wins, no corruption.
    #
    # on_progress writes (sims_done, ci_half) to the job record every
    # _PROGRESS_INTERVAL sims. Worst-case: _MAX_SIMS/_PROGRESS_INTERVAL = 100
    # DynamoDB writes per job. Pollers read these fields while status=running.
    league = _league_from_dict(job["league"])
    seed: int | None = int(job["seed"]) if job.get("seed") is not None else None

    def _on_progress(sims_done: int, ci_half: float) -> None:
        job_store.update_progress(job_id, sims_done, ci_half)

    result = simulate(
        team_a_players, team_a_lineup,
        team_b_players, team_b_lineup,
        league,
        seed=seed,
        on_progress=_on_progress,
    )

    # ── 5. Write result (RUNNING → DONE) ─────────────────────────────────────
    job_store.write_result(job_id, result, completed_at=_now_iso())

    # ── 6. Acknowledge — message deleted only on clean success ────────────────
    # If the process dies between step 4 and here, the message is not deleted.
    # VisibilityTimeout expires → SQS retries. The step-1 idempotency check
    # above then skips the re-run immediately.
    queue_client.delete(receipt_handle)
    return "done"


# ── Polling loop (local invocation shell) ─────────────────────────────────────


def polling_loop(
    queue_client: QueueClient | None = None,
    job_store: JobStore | None = None,
) -> None:
    """
    Block forever, polling the main queue and calling handle_job on each message.

    This is the local equivalent of the Lambda trigger. One process = one worker.
    Parallelise by running N copies of this script (each is independent).

    Run: MY5_ENV=local python -m my5.job_worker
    """
    if queue_client is None:
        queue_client = QueueClient()
    if job_store is None:
        job_store = JobStore()
    dynamo = make_dynamo_resource()

    print(f"[worker] {_WORKER_ID} — polling {queue_client._queue_url}")
    while True:
        messages = queue_client.receive(wait_seconds=20, visibility_timeout=60)
        for msg in messages:
            job_id = msg["job_id"]
            print(f"[worker] received job_id={job_id} (attempt #{msg['receive_count']})")
            try:
                status = handle_job(
                    job_id, msg["receipt_handle"],
                    queue_client=queue_client,
                    job_store=job_store,
                    dynamo_resource=dynamo,
                )
                print(f"[worker] job_id={job_id} → {status}")
            except Exception as exc:
                # Do NOT delete the message — let VisibilityTimeout expire for retry.
                print(f"[worker] ERROR job_id={job_id}: {exc!r} — leaving for retry")


# ── DLQ monitor (local invocation shell; event-driven Lambda on AWS) ──────────


def dlq_monitor_loop(
    queue_client: QueueClient | None = None,
    job_store: JobStore | None = None,
) -> None:
    """
    Poll the DLQ and mark corresponding jobs as FAILED.

    LOCAL: runs as a polling loop (this function). Equivalent to the second Lambda
    function on AWS that is triggered by the DLQ instead of the main queue.

    AWS PORT: write a lambda_handler(event, ctx) that iterates event["Records"]
    and calls _handle_dlq_message for each — identical core logic, different shell.
    The Lambda is event-driven: $0 idle cost (only runs when DLQ has messages).

    Run: MY5_ENV=local python -m my5.job_worker --dlq
    """
    if queue_client is None:
        queue_client = QueueClient()
    if job_store is None:
        job_store = JobStore()
    dlq = queue_client.dlq_url

    print(f"[dlq-monitor] {_WORKER_ID} — polling {dlq}")
    while True:
        messages = queue_client.receive(
            queue_url=dlq, wait_seconds=20, visibility_timeout=30
        )
        for msg in messages:
            job_id = msg["job_id"]
            print(f"[dlq-monitor] job_id={job_id} exhausted retries — marking FAILED")
            try:
                job_store.fail_job(
                    job_id,
                    error_type="engine_error",
                    error_message="Job exhausted 3 delivery attempts without success.",
                    completed_at=_now_iso(),
                )
                queue_client.delete(msg["receipt_handle"], queue_url=dlq)
                print(f"[dlq-monitor] job_id={job_id} → failed")
            except Exception as exc:
                print(f"[dlq-monitor] ERROR job_id={job_id}: {exc!r}")


# ── Entry point ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys
    if "--dlq" in sys.argv:
        dlq_monitor_loop()
    else:
        polling_loop()
