"""
DynamoDBRegistry: Registry interface backed by the my5-ws-connections DynamoDB table.

Table schema (PAY_PER_REQUEST, $0 idle):
  PK:  conn_id (S)  — O(1) register/unregister (put_item / delete_item)
  GSI: job_id-index (PK=job_id S, projection ALL) — O(1) lookup(job_id) via query

AWS analog of ws/registry.py (in-memory dict). Same three method signatures;
callers (push_progress, ws_handler) are unchanged — only the injected object differs.

Uses the DynamoDB low-level client (not the resource API) so KeyConditionExpression
is a plain string. String expressions are easier to fake in tests without importing
boto3.dynamodb.conditions Key objects.
"""
from __future__ import annotations

from typing import Any

import boto3

_TABLE = "my5-ws-connections"
_GSI = "job_id-index"


class DynamoDBRegistry:
    """
    Thread-safe (DynamoDB itself provides atomicity) registry backed by DynamoDB.

    Inject `client` in tests to avoid real network calls.
    """

    def __init__(self, client: Any = None, region_name: str = "us-east-1") -> None:
        self._client = client or boto3.client("dynamodb", region_name=region_name)

    def register(self, job_id: str, conn_id: str) -> None:
        """PutItem: write {conn_id, job_id} — O(1) by PK."""
        self._client.put_item(
            TableName=_TABLE,
            Item={"conn_id": {"S": conn_id}, "job_id": {"S": job_id}},
        )

    def lookup(self, job_id: str) -> list[str]:
        """Query GSI job_id-index → list of conn_ids watching this job."""
        resp = self._client.query(
            TableName=_TABLE,
            IndexName=_GSI,
            KeyConditionExpression="job_id = :jid",
            ExpressionAttributeValues={":jid": {"S": job_id}},
        )
        return [item["conn_id"]["S"] for item in resp.get("Items", [])]

    def unregister(self, conn_id: str) -> None:
        """DeleteItem by PK — idempotent: deleting a missing item is a no-op in DynamoDB."""
        self._client.delete_item(
            TableName=_TABLE,
            Key={"conn_id": {"S": conn_id}},
        )
