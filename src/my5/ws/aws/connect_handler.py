"""
Lambda handler for API Gateway WebSocket $connect and $disconnect routes.

$connect   → register(job_id, conn_id)  in DynamoDBRegistry
$disconnect → unregister(conn_id)       in DynamoDBRegistry
$default   → 200 (ignored; clients don't send messages — server-push only)

job_id comes from the WebSocket URL query string:
    wss://{api_id}.execute-api.{region}.amazonaws.com/{stage}?job_id={uuid}

Returns:
  200 OK         on success
  400 Bad Request if $connect is missing job_id
  500 Internal   on unexpected DynamoDB error

Lambda best practice: module-level singleton (_registry) avoids re-creating the
boto3 client on warm invocations. Tests bypass via patch("...._get_registry").
"""
from __future__ import annotations

from typing import Any

from my5.ws.aws.connections_table import DynamoDBRegistry

_registry: DynamoDBRegistry | None = None


def _get_registry() -> DynamoDBRegistry:
    global _registry
    if _registry is None:
        _registry = DynamoDBRegistry()
    return _registry


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint — handles $connect, $disconnect, and $default route keys."""
    ctx = event.get("requestContext", {})
    route_key = ctx.get("routeKey", "")
    conn_id: str = ctx.get("connectionId", "")
    registry = _get_registry()

    if route_key == "$connect":
        params = event.get("queryStringParameters") or {}
        job_id: str = params.get("job_id", "")
        if not job_id:
            return {"statusCode": 400, "body": "Missing job_id query parameter"}
        try:
            registry.register(job_id, conn_id)
        except Exception as exc:
            return {"statusCode": 500, "body": str(exc)}
        return {"statusCode": 200, "body": "Connected"}

    if route_key == "$disconnect":
        try:
            registry.unregister(conn_id)
        except Exception as exc:
            return {"statusCode": 500, "body": str(exc)}
        return {"statusCode": 200, "body": "Disconnected"}

    # $default or any other route — server-push only, no client messages expected
    return {"statusCode": 200, "body": "OK"}
