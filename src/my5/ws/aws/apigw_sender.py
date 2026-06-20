"""
ApiGwSender: Sender protocol backed by API Gateway Management API post_to_connection.

AWS twin of LocalSender (LocalSender routes payload to asyncio.Queue;
this routes to a live WebSocket connection via APIGW).

Conforms to the Sender Protocol from ws/push.py — push_progress is unchanged.

GoneException (HTTP 410) → return GONE sentinel so push_progress prunes the conn_id.
All other errors propagate.

Note: post_to_connection is a synchronous boto3 call inside an async method.
In Lambda (single-threaded, no shared event loop), blocking is acceptable.
For high-concurrency use, replace with asyncio.to_thread(self._client.post_to_connection, ...).
"""
from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import ClientError

from my5.ws.push import GONE, _GoneSentinel


class ApiGwSender:
    """
    Sender backed by API Gateway Management API.

    endpoint_url: full management endpoint, e.g.
        "https://{api_id}.execute-api.{region}.amazonaws.com/{stage}"
    Available as APIGW_ENDPOINT env var in the Lambda execution context.
    """

    def __init__(self, endpoint_url: str, client: Any = None) -> None:
        self._client = client or boto3.client(
            "apigatewaymanagementapi", endpoint_url=endpoint_url
        )

    async def send(self, conn_id: str, payload: str) -> _GoneSentinel | None:
        """
        Post payload to a WebSocket connection.

        Returns GONE if the connection is no longer active (HTTP 410).
        Raises ClientError for all other AWS-side errors.
        """
        try:
            self._client.post_to_connection(
                ConnectionId=conn_id,
                Data=payload.encode("utf-8"),
            )
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "GoneException":
                return GONE
            raise
