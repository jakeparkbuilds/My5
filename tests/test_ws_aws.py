"""
Tests for the AWS WebSocket backend (P2-C AWS shell).

Five groups:

  1. DynamoDBRegistry — same behavioral contract as the in-memory Registry,
     against a FakeDynamoClient (no real network).
  2. ApiGwSender — mock post_to_connection: success → None; 410 → GONE; other → raises.
  3. connect_handler — $connect/$disconnect Lambda events; fake DynamoDBRegistry injected.
  4. fanout_handler — hand-built DynamoDB Streams NewImage records (running/done/failed/REMOVE).
  5. Shared emitter — job_record_to_message produces identical output for plain-Python
     dict (local path) and TypeDeserializer output (Streams path); proves no fork.
     Also asserts server._job_to_message IS the same function object as emit.job_record_to_message.

All 67 existing tests must still pass unchanged.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Shared helpers ────────────────────────────────────────────────────────────


class FakeDynamoClient:
    """
    In-memory DynamoDB client stub for DynamoDBRegistry tests.

    Stores items as DynamoDB JSON dicts ({"S": ...}, {"N": ...}) matching the
    client API format used by DynamoDBRegistry.
    """

    def __init__(self) -> None:
        # conn_id → {"conn_id": {"S": ...}, "job_id": {"S": ...}}
        self._items: dict[str, dict[str, Any]] = {}

    def put_item(self, *, TableName: str, Item: dict) -> dict:
        conn_id = Item["conn_id"]["S"]
        self._items[conn_id] = {k: dict(v) for k, v in Item.items()}
        return {}

    def delete_item(self, *, TableName: str, Key: dict) -> dict:
        conn_id = Key["conn_id"]["S"]
        self._items.pop(conn_id, None)
        return {}

    def query(
        self,
        *,
        TableName: str,
        IndexName: str,
        KeyConditionExpression: str,
        ExpressionAttributeValues: dict,
    ) -> dict:
        job_id = ExpressionAttributeValues[":jid"]["S"]
        items = [
            item
            for item in self._items.values()
            if item.get("job_id", {}).get("S") == job_id
        ]
        return {"Items": items}


# ── Group 1: DynamoDBRegistry ─────────────────────────────────────────────────


def test_dynamo_registry_register_and_lookup() -> None:
    """register() puts item; lookup() queries GSI and returns conn_id list."""
    from my5.ws.aws.connections_table import DynamoDBRegistry

    client = FakeDynamoClient()
    reg = DynamoDBRegistry(client=client)

    assert reg.lookup("job1") == []

    reg.register("job1", "conn1")
    assert reg.lookup("job1") == ["conn1"]


def test_dynamo_registry_multi_watcher() -> None:
    """Two connections watching the same job both appear in lookup."""
    from my5.ws.aws.connections_table import DynamoDBRegistry

    client = FakeDynamoClient()
    reg = DynamoDBRegistry(client=client)

    reg.register("job2", "connA")
    reg.register("job2", "connB")
    assert set(reg.lookup("job2")) == {"connA", "connB"}

    # Other job is independent.
    reg.register("job3", "connC")
    assert reg.lookup("job3") == ["connC"]
    assert "connC" not in reg.lookup("job2")


def test_dynamo_registry_unregister_and_idempotent() -> None:
    """unregister() removes conn; second unregister of same conn is a no-op."""
    from my5.ws.aws.connections_table import DynamoDBRegistry

    client = FakeDynamoClient()
    reg = DynamoDBRegistry(client=client)

    reg.register("job4", "connX")
    reg.register("job4", "connY")
    reg.unregister("connX")
    assert reg.lookup("job4") == ["connY"]

    # Second unregister of the same conn_id is idempotent.
    reg.unregister("connX")
    assert reg.lookup("job4") == ["connY"]

    # Unregister last watcher — bucket is empty.
    reg.unregister("connY")
    assert reg.lookup("job4") == []


# ── Group 2: ApiGwSender ──────────────────────────────────────────────────────


def test_apigw_sender_success_returns_none() -> None:
    """Successful post_to_connection → send() returns None."""

    async def run() -> None:
        from my5.ws.aws.apigw_sender import ApiGwSender

        mock_client = MagicMock()
        mock_client.post_to_connection.return_value = {}

        sender = ApiGwSender(endpoint_url="https://fake.execute-api.us-east-1.amazonaws.com/prod", client=mock_client)
        result = await sender.send("conn1", '{"type": "progress"}')
        assert result is None
        mock_client.post_to_connection.assert_called_once_with(
            ConnectionId="conn1",
            Data=b'{"type": "progress"}',
        )

    asyncio.run(run())


def test_apigw_sender_gone_returns_gone_sentinel() -> None:
    """HTTP 410 GoneException → send() returns GONE sentinel."""

    async def run() -> None:
        from botocore.exceptions import ClientError
        from my5.ws.aws.apigw_sender import ApiGwSender
        from my5.ws.push import GONE

        mock_client = MagicMock()
        mock_client.post_to_connection.side_effect = ClientError(
            {"Error": {"Code": "GoneException", "Message": "Connection gone"}},
            "PostToConnection",
        )

        sender = ApiGwSender(endpoint_url="https://fake.execute-api.us-east-1.amazonaws.com/prod", client=mock_client)
        result = await sender.send("dead-conn", '{"type": "done"}')
        assert result is GONE

    asyncio.run(run())


def test_apigw_sender_other_error_raises() -> None:
    """Non-410 ClientError propagates as-is."""

    async def run() -> None:
        from botocore.exceptions import ClientError
        from my5.ws.aws.apigw_sender import ApiGwSender

        mock_client = MagicMock()
        mock_client.post_to_connection.side_effect = ClientError(
            {"Error": {"Code": "LimitExceededException", "Message": "Too many calls"}},
            "PostToConnection",
        )

        sender = ApiGwSender(endpoint_url="https://fake.execute-api.us-east-1.amazonaws.com/prod", client=mock_client)
        with pytest.raises(ClientError):
            await sender.send("conn1", "{}")

    asyncio.run(run())


# ── Group 3: connect_handler ──────────────────────────────────────────────────


class _FakeRegistry:
    """Minimal Registry stub that records register/unregister calls."""

    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.unregistered: list[str] = []

    def register(self, job_id: str, conn_id: str) -> None:
        self.registered.append((job_id, conn_id))

    def unregister(self, conn_id: str) -> None:
        self.unregistered.append(conn_id)

    def lookup(self, job_id: str) -> list[str]:
        return []


def test_connect_handler_connect_registers_and_returns_200() -> None:
    """`$connect` with job_id → register(job_id, conn_id), HTTP 200."""
    from my5.ws.aws.connect_handler import handler

    fake_reg = _FakeRegistry()
    event = {
        "requestContext": {"routeKey": "$connect", "connectionId": "conn-abc"},
        "queryStringParameters": {"job_id": "job-xyz"},
    }

    with patch("my5.ws.aws.connect_handler._get_registry", return_value=fake_reg):
        resp = handler(event, None)

    assert resp["statusCode"] == 200
    assert fake_reg.registered == [("job-xyz", "conn-abc")]


def test_connect_handler_connect_missing_job_id_returns_400() -> None:
    """`$connect` with no job_id → 400, nothing registered."""
    from my5.ws.aws.connect_handler import handler

    fake_reg = _FakeRegistry()
    event = {
        "requestContext": {"routeKey": "$connect", "connectionId": "conn-abc"},
        "queryStringParameters": {},
    }

    with patch("my5.ws.aws.connect_handler._get_registry", return_value=fake_reg):
        resp = handler(event, None)

    assert resp["statusCode"] == 400
    assert fake_reg.registered == []


def test_connect_handler_disconnect_unregisters_and_returns_200() -> None:
    """`$disconnect` → unregister(conn_id), HTTP 200."""
    from my5.ws.aws.connect_handler import handler

    fake_reg = _FakeRegistry()
    event = {
        "requestContext": {"routeKey": "$disconnect", "connectionId": "conn-abc"},
    }

    with patch("my5.ws.aws.connect_handler._get_registry", return_value=fake_reg):
        resp = handler(event, None)

    assert resp["statusCode"] == 200
    assert fake_reg.unregistered == ["conn-abc"]


# ── Group 4: fanout_handler ───────────────────────────────────────────────────


def _stream_record(event_name: str, new_image: dict) -> dict:
    """Build a minimal DynamoDB Streams record."""
    return {"eventName": event_name, "dynamodb": {"NewImage": new_image}}


def _dynamo_str(v: str) -> dict:
    return {"S": v}


def _dynamo_num(v: str) -> dict:
    return {"N": v}


def _dynamo_bool(v: bool) -> dict:
    return {"BOOL": v}


def _dynamo_map(d: dict) -> dict:
    return {"M": d}


def test_fanout_handler_progress_record() -> None:
    """MODIFY with status=running fires push_progress with type=progress."""
    from my5.ws.aws import fanout_handler

    captured: list[tuple] = []

    async def fake_push(job_id: str, message: dict, registry: Any, sender: Any) -> None:
        captured.append((job_id, message))

    record = _stream_record("MODIFY", {
        "job_id": _dynamo_str("job1"),
        "status": _dynamo_str("running"),
        "progress_sims": _dynamo_num("50"),
        "progress_ci": _dynamo_num("4.2"),
    })

    with (
        patch.object(fanout_handler, "push_progress", fake_push),
        patch.object(fanout_handler, "DynamoDBRegistry", MagicMock),
        patch.object(fanout_handler, "ApiGwSender", MagicMock),
        patch.dict("os.environ", {"APIGW_ENDPOINT": "https://fake.execute-api.us-east-1.amazonaws.com/prod"}),
    ):
        fanout_handler.handler({"Records": [record]}, None)

    assert len(captured) == 1
    jid, msg = captured[0]
    assert jid == "job1"
    assert msg["type"] == "progress"
    assert msg["sims_done"] == 50
    assert abs(msg["ci_half"] - 4.2) < 1e-9


def test_fanout_handler_done_record() -> None:
    """MODIFY with status=done fires push_progress with type=done and full SimResult fields."""
    from my5.ws.aws import fanout_handler

    captured: list[tuple] = []

    async def fake_push(job_id: str, message: dict, registry: Any, sender: Any) -> None:
        captured.append((job_id, message))

    result_map = {
        "n_sims": _dynamo_num("258"),
        "mean_margin": _dynamo_num("1.79"),
        "ci_half_width": _dynamo_num("1.95"),
        "equiv_net_rating": _dynamo_num("3.6"),
        "converged": _dynamo_bool(True),
    }
    record = _stream_record("MODIFY", {
        "job_id": _dynamo_str("job2"),
        "status": _dynamo_str("done"),
        "result": _dynamo_map(result_map),
    })

    with (
        patch.object(fanout_handler, "push_progress", fake_push),
        patch.object(fanout_handler, "DynamoDBRegistry", MagicMock),
        patch.object(fanout_handler, "ApiGwSender", MagicMock),
        patch.dict("os.environ", {"APIGW_ENDPOINT": "https://fake.execute-api.us-east-1.amazonaws.com/prod"}),
    ):
        fanout_handler.handler({"Records": [record]}, None)

    assert len(captured) == 1
    jid, msg = captured[0]
    assert jid == "job2"
    assert msg["type"] == "done"
    assert msg["n_sims"] == 258
    assert abs(msg["mean_margin"] - 1.79) < 1e-9
    assert msg["converged"] is True


def test_fanout_handler_failed_record() -> None:
    """MODIFY with status=failed fires push_progress with type=failed."""
    from my5.ws.aws import fanout_handler

    captured: list[tuple] = []

    async def fake_push(job_id: str, message: dict, registry: Any, sender: Any) -> None:
        captured.append((job_id, message))

    record = _stream_record("MODIFY", {
        "job_id": _dynamo_str("job3"),
        "status": _dynamo_str("failed"),
        "error_type": _dynamo_str("invalid_lineup"),
        "error_message": _dynamo_str("Player 999 not found"),
    })

    with (
        patch.object(fanout_handler, "push_progress", fake_push),
        patch.object(fanout_handler, "DynamoDBRegistry", MagicMock),
        patch.object(fanout_handler, "ApiGwSender", MagicMock),
        patch.dict("os.environ", {"APIGW_ENDPOINT": "https://fake.execute-api.us-east-1.amazonaws.com/prod"}),
    ):
        fanout_handler.handler({"Records": [record]}, None)

    assert len(captured) == 1
    jid, msg = captured[0]
    assert jid == "job3"
    assert msg["type"] == "failed"
    assert msg["error_type"] == "invalid_lineup"


def test_fanout_handler_remove_record_ignored() -> None:
    """REMOVE events are ignored; push_progress is never called."""
    from my5.ws.aws import fanout_handler

    captured: list[tuple] = []

    async def fake_push(job_id: str, message: dict, registry: Any, sender: Any) -> None:
        captured.append((job_id, message))

    record = {
        "eventName": "REMOVE",
        "dynamodb": {"OldImage": {"job_id": _dynamo_str("job4")}},
    }

    with (
        patch.object(fanout_handler, "push_progress", fake_push),
        patch.object(fanout_handler, "DynamoDBRegistry", MagicMock),
        patch.object(fanout_handler, "ApiGwSender", MagicMock),
        patch.dict("os.environ", {"APIGW_ENDPOINT": "https://fake.execute-api.us-east-1.amazonaws.com/prod"}),
    ):
        fanout_handler.handler({"Records": [record]}, None)

    assert captured == [], "REMOVE events must not trigger push_progress"


# ── Group 5: Shared emitter ───────────────────────────────────────────────────


def test_shared_emitter_server_and_emit_are_same_function() -> None:
    """
    server._job_to_message IS emit.job_record_to_message — the exact same function object.
    Proves the refactor didn't fork the emitter; both callers share one code path.
    """
    from my5.ws.emit import job_record_to_message
    from my5.ws.server import _job_to_message

    assert _job_to_message is job_record_to_message


def test_shared_emitter_plain_dict_and_streams_image_produce_same_output() -> None:
    """
    job_record_to_message produces identical output for:
      - plain Python dict (local path: JobStore → _dec_to_float)
      - TypeDeserializer output (AWS path: Streams NewImage)

    This proves the emitter handles both Decimal (Streams) and float/int (local)
    numeric types via int()/float()/bool() coercions.
    """
    from boto3.dynamodb.types import TypeDeserializer
    from my5.ws.emit import job_record_to_message

    deserializer = TypeDeserializer()

    # ── Test case 1: done record ───────────────────────────────────────────────
    streams_image = {
        "job_id": {"S": "abc123"},
        "status": {"S": "done"},
        "result": {
            "M": {
                "n_sims":           {"N": "258"},
                "mean_margin":      {"N": "1.79"},
                "ci_half_width":    {"N": "1.95"},
                "equiv_net_rating": {"N": "3.6"},
                "converged":        {"BOOL": True},
            }
        },
    }
    # AWS path: TypeDeserializer output
    streams_job = {k: deserializer.deserialize(v) for k, v in streams_image.items()}
    streams_msg = job_record_to_message(streams_job)

    # Local path: plain Python dict (as returned by JobStore + _dec_to_float)
    local_job = {
        "status": "done",
        "result": {
            "n_sims": 258,
            "mean_margin": 1.79,
            "ci_half_width": 1.95,
            "equiv_net_rating": 3.6,
            "converged": True,
        },
    }
    local_msg = job_record_to_message(local_job)

    assert streams_msg == local_msg, (
        f"Emitter diverged between Streams and local input:\n"
        f"  streams: {streams_msg}\n"
        f"  local:   {local_msg}"
    )
    assert streams_msg["type"] == "done"
    assert streams_msg["n_sims"] == 258
    assert abs(streams_msg["mean_margin"] - 1.79) < 1e-9
    assert streams_msg["converged"] is True

    # ── Test case 2: progress record ───────────────────────────────────────────
    progress_streams = {
        "job_id":        {"S": "abc123"},
        "status":        {"S": "running"},
        "progress_sims": {"N": "100"},
        "progress_ci":   {"N": "3.25"},
    }
    streams_job2 = {k: deserializer.deserialize(v) for k, v in progress_streams.items()}
    streams_msg2 = job_record_to_message(streams_job2)

    local_job2 = {"status": "running", "progress_sims": 100, "progress_ci": 3.25}
    local_msg2 = job_record_to_message(local_job2)

    assert streams_msg2 == local_msg2
    assert streams_msg2["type"] == "progress"
    assert streams_msg2["sims_done"] == 100

    # ── Test case 3: failed record ─────────────────────────────────────────────
    failed_streams = {
        "job_id":        {"S": "abc123"},
        "status":        {"S": "failed"},
        "error_type":    {"S": "invalid_lineup"},
        "error_message": {"S": "Player not found"},
    }
    streams_job3 = {k: deserializer.deserialize(v) for k, v in failed_streams.items()}
    assert job_record_to_message(streams_job3) == job_record_to_message(
        {"status": "failed", "error_type": "invalid_lineup", "error_message": "Player not found"}
    )
