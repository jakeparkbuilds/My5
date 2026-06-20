"""
NotifyingJobStore: wraps JobStore and publishes progress events to the bus.

This is the local analog of DynamoDB Streams. On AWS:
  - handle_job gets a plain JobStore
  - Streams picks up every update_item write and triggers the fan-out Lambda

Locally:
  - handle_job gets a NotifyingJobStore
  - update_progress/write_result/fail_job each call through to the inner
    store AND post the corresponding event to the bus

handle_job is BYTE-IDENTICAL in both cases — it only calls store.update_progress,
store.write_result, etc. It does not know whether the store notifies. This is
the exact seam pattern used for ElasticMQ (local) vs SQS (AWS) on the queue.

ATOMIC WRITE CONFIRMED: job_store.write_result issues ONE update_item call that
sets status="done", the result map, and completed_at atomically. The bus event
is posted after that single write, so the job record is always consistent
(status + result present) before any subscriber reads it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from my5.simulator import SimResult
    from my5.ws.bus import EventBus


class NotifyingJobStore:
    """
    Wraps a JobStore and publishes progress/terminal events to an EventBus.

    Only the three write methods that carry observable state changes send
    events: update_progress, write_result, fail_job. All other methods
    (put_job, get_job, update_status) delegate silently.
    """

    def __init__(self, inner: Any, bus: "EventBus") -> None:
        self._inner = inner
        self._bus = bus

    # ── Notifying writes ──────────────────────────────────────────────────────

    def update_progress(self, job_id: str, sims_done: int, ci_half: float) -> None:
        self._inner.update_progress(job_id, sims_done, ci_half)
        self._bus.post_threadsafe({
            "job_id": job_id,
            "message": {
                "type": "progress",
                "sims_done": sims_done,
                "ci_half": ci_half,
            },
        })

    def write_result(self, job_id: str, result: "SimResult", completed_at: str) -> None:
        # Inner write is atomic (one update_item: status + result + completed_at).
        self._inner.write_result(job_id, result, completed_at)
        self._bus.post_threadsafe({
            "job_id": job_id,
            "message": {
                "type": "done",
                "n_sims": result.n_sims,
                "mean_margin": result.mean_margin,
                "ci_half_width": result.ci_half_width,
                "equiv_net_rating": result.equiv_net_rating,
                "converged": result.converged,
            },
        })

    def fail_job(
        self,
        job_id: str,
        error_type: str,
        error_message: str,
        completed_at: str,
    ) -> None:
        self._inner.fail_job(job_id, error_type, error_message, completed_at)
        self._bus.post_threadsafe({
            "job_id": job_id,
            "message": {
                "type": "failed",
                "error_type": error_type,
                "error_message": error_message,
            },
        })

    # ── Silent delegation ─────────────────────────────────────────────────────

    def put_job(self, item: dict[str, Any]) -> None:
        self._inner.put_job(item)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._inner.get_job(job_id)

    def update_status(self, *args: Any, **kwargs: Any) -> None:
        self._inner.update_status(*args, **kwargs)
