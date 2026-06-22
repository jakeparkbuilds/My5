"""
Lambda handler for SQS-triggered simulation jobs (AWS path).

Invocation shell only — the core handle_job function is imported unchanged
from job_worker.py. This mirrors the local polling_loop structure:

  LOCAL:                         LAMBDA (AWS):
  polling_loop()                 handler(event, context)
    └─ receive SQS message         └─ iterate event["Records"]
         └─ handle_job()               └─ handle_job()
              ↑                              ↑
              same function, same logic ─────┘

SQS event source mapping (batch_size=1): Lambda receives one message per
invocation. On function success, Lambda's event source mapping deletes the
message. handle_job also deletes it explicitly — the double-delete is
idempotent and harmless (SQS receipts invalidate after first delete).

On function exception: Lambda does NOT delete the message. SQS
VisibilityTimeout expires and the message becomes visible for retry.
After maxReceiveCount=3 failures, SQS routes to my5-jobs-dlq.

Module-level singletons (QueueClient, JobStore, SimCache) are initialized
once per Lambda container and reused across warm invocations.
"""
from __future__ import annotations

import json
from typing import Any

from my5.cache import SimCache
from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.queue_client import QueueClient

_queue_client: QueueClient | None = None
_job_store: JobStore | None = None
_cache: SimCache | None = None


def _get_clients() -> tuple[QueueClient, JobStore, SimCache]:
    global _queue_client, _job_store, _cache
    if _queue_client is None:
        _queue_client = QueueClient()
    if _job_store is None:
        _job_store = JobStore()
    if _cache is None:
        _cache = SimCache()
    return _queue_client, _job_store, _cache


def handler(event: dict[str, Any], context: Any) -> None:
    """Lambda entrypoint — processes one SQS message per invocation."""
    queue_client, job_store, cache = _get_clients()
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        job_id: str = body["job_id"]
        receipt_handle: str = record["receiptHandle"]
        handle_job(
            job_id,
            receipt_handle,
            queue_client=queue_client,
            job_store=job_store,
            cache=cache,
        )
