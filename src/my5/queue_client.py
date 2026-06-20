"""
SQS wrapper for job submission and acknowledgment.

Two operations are the entire interface:
  enqueue(job_id)             → puts {"job_id": job_id} in the queue
  receive(queue_url=...)      → long-poll, return [{job_id, receipt_handle}, ...]
  delete(receipt_handle, ...) → delete a message after processing

The local queue (ElasticMQ) and real SQS use identical boto3 calls — only the
endpoint_url differs, which is set in config.py. No code change for AWS deployment.
"""
from __future__ import annotations

import json
from typing import Any

from my5.config import get_sqs_queue_url, make_sqs_client


class QueueClient:
    """
    Thin wrapper over an SQS queue.

    Pass a fake `sqs_client` and explicit `queue_url` in tests to avoid
    real network calls:
        client = QueueClient(sqs_client=FakeSQS(), queue_url="fake://q")

    The `sqs_client` argument is a boto3 SQS client (or duck-typed equivalent
    implementing send_message, receive_message, delete_message).
    """

    def __init__(
        self,
        sqs_client: Any = None,
        queue_url: str | None = None,
    ) -> None:
        self._sqs = sqs_client if sqs_client is not None else make_sqs_client()
        self._queue_url = queue_url if queue_url is not None else get_sqs_queue_url("my5-jobs")

    def enqueue(self, job_id: str) -> str:
        """
        Send job_id to the queue. Message body is {"job_id": job_id} — a pointer only.
        All job parameters live in DynamoDB (my5-sim-jobs); SQS carries nothing else.

        Returns the SQS MessageId.
        """
        resp = self._sqs.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps({"job_id": job_id}),
        )
        return resp["MessageId"]

    def receive(
        self,
        *,
        queue_url: str | None = None,
        max_messages: int = 1,
        wait_seconds: int = 20,
        visibility_timeout: int = 60,
    ) -> list[dict[str, str]]:
        """
        Long-poll for messages. Returns a list of {job_id, receipt_handle} dicts.

        wait_seconds=20 is the SQS long-polling maximum — this keeps the worker
        idle and CPU-free when the queue is empty (no busy-polling).
        visibility_timeout=60 matches the queue's configured VisibilityTimeout.

        queue_url: override to poll from the DLQ instead of the main queue.
        """
        url = queue_url if queue_url is not None else self._queue_url
        resp = self._sqs.receive_message(
            QueueUrl=url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        result = []
        for msg in messages:
            body = json.loads(msg["Body"])
            result.append({
                "job_id": body["job_id"],
                "receipt_handle": msg["ReceiptHandle"],
                "receive_count": int(
                    msg.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                ),
            })
        return result

    def delete(self, receipt_handle: str, *, queue_url: str | None = None) -> None:
        """
        Delete a message after successful processing (or after failing fast).
        Must be called exactly once per successfully handled message — never on
        transient errors (let VisibilityTimeout expire for automatic retry).

        queue_url: override to delete from the DLQ instead of the main queue.
        """
        url = queue_url if queue_url is not None else self._queue_url
        self._sqs.delete_message(
            QueueUrl=url,
            ReceiptHandle=receipt_handle,
        )

    @property
    def dlq_url(self) -> str:
        return get_sqs_queue_url("my5-jobs-dlq")
