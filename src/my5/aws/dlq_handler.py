"""
Lambda handler for the my5-jobs-dlq dead-letter queue (AWS path).

Triggered when a job exhausts its 3 SQS delivery attempts without succeeding.
Marks the corresponding job record as FAILED so the frontend can surface the
error instead of waiting forever.

This is the AWS analog of the local dlq_monitor_loop in job_worker.py.
The core operation (job_store.fail_job) is byte-identical on both targets.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

from my5.job_store import JobStore

_job_store: JobStore | None = None


def _get_job_store() -> JobStore:
    global _job_store
    if _job_store is None:
        _job_store = JobStore()
    return _job_store


def handler(event: dict[str, Any], context: Any) -> None:
    """Lambda entrypoint — marks each dead-lettered job as FAILED."""
    job_store = _get_job_store()
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        job_id: str = body["job_id"]
        completed_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        job_store.fail_job(
            job_id,
            error_type="engine_error",
            error_message="Job exhausted 3 SQS delivery attempts without success.",
            completed_at=completed_at,
        )
