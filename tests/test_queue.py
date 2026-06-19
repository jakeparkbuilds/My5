"""
Unit tests for the async job flow: submit_job → handle_job.

No real network calls. JobStore and QueueClient are replaced with in-memory
fakes that expose the same interface. The engine (simulate()) is called for real
in test_handle_job_valid_reaches_done to verify that the stored result matches a
direct engine call with the same seed.

Five tests:
  1. submit_job writes a QUEUED record + enqueues exactly one pointer message.
  2. handle_job on a valid job: transitions QUEUED→RUNNING→DONE; result matches
     a direct simulate() call with the same seed.
  3. handle_job on a job that is already DONE: deletes the message, returns
     "skipped", does NOT re-run the engine.
  4. handle_job on a job with an invalid lineup (fetch_lineup raises
     LineupNotFoundError): status=FAILED, error_type=invalid_lineup, msg deleted.
  5. handle_job raises before deleting the message (simulated mid-job crash):
     the message is NOT deleted (VisibilityTimeout will expire → SQS retries).
"""
from __future__ import annotations

import dataclasses
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest

from my5.job_store import JobStore, LineupNotFoundError
from my5.job_worker import handle_job
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, simulate
from my5.submit_job import _DEFAULT_LEAGUE, _serialize_league, submit_job

# ── Shared fixtures ───────────────────────────────────────────────────────────


_LEAGUE = LeagueAverages(
    usage_rate=0.19,
    rim_fg_pct=0.616,
    mid_fg_pct=0.410,
    fg3_pct=0.379,
    tov_rate=0.1119,
    ft_rate=0.038,
    ft_pct=0.770,
    oreb_rate=0.065,
    shot_rim_rate=0.450,
    shot_mid_rate=0.175,
    shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616,
    opp_mid_fg_pct=0.410,
    opp_3p_fg_pct=0.379,
    forced_to_rate=0.126,
    dreb_rate=0.730,
)


def _make_player(
    usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
    shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
    rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
) -> dict:
    return {
        "usage_rate": usage_rate, "tov_rate": tov_rate,
        "ft_rate": ft_rate, "ft_pct": ft_pct,
        "shot_rim_rate": shot_rim_rate, "shot_mid_rate": shot_mid_rate,
        "shot_3p_rate": shot_3p_rate,
        "rim_fg_pct": rim_fg_pct, "mid_fg_pct": mid_fg_pct, "fg3_pct": fg3_pct,
        "oreb_rate": oreb_rate,
    }


def _five_players() -> list[dict]:
    return [_make_player() for _ in range(5)]


def _sample_lineup() -> dict:
    return {
        "lineup_key": "test_key",
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to": 12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55, "dreb_opp": 75, "dreb_rate": 0.733,
    }


# ── In-memory fakes ───────────────────────────────────────────────────────────


class FakeTable:
    """Minimal in-memory DynamoDB table that satisfies the JobStore interface."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def put_item(self, *, Item: dict) -> dict:
        from my5.job_store import _dec_to_float
        self._items[Item["job_id"]] = _dec_to_float(Item)
        return {}

    def get_item(self, *, Key: dict) -> dict:
        job_id = Key["job_id"]
        if job_id not in self._items:
            return {}
        return {"Item": dict(self._items[job_id])}

    def update_item(
        self, *, Key: dict, UpdateExpression: str,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
    ) -> dict:
        job_id = Key["job_id"]
        item = self._items.setdefault(job_id, {"job_id": job_id})
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        # Resolve aliases
        def _resolve(name: str) -> str:
            return names.get(name, name)

        def _value(ph: str) -> Any:
            v = values[ph]
            if isinstance(v, Decimal):
                return float(v)
            return v

        # Parse "SET a = :a, b = b + :c" style (covers our usage patterns)
        body = UpdateExpression.strip()
        if body.upper().startswith("SET "):
            body = body[4:]
        for clause in body.split(","):
            clause = clause.strip()
            if "= " in clause and " + " in clause:
                # Increment: "attempt_count = attempt_count + :one"
                lhs, rhs = clause.split("=", 1)
                field = _resolve(lhs.strip())
                _, inc_ph = rhs.split("+")
                item[field] = item.get(field, 0) + _value(inc_ph.strip())
            else:
                lhs, rhs = clause.split("=", 1)
                field = _resolve(lhs.strip())
                item[field] = _value(rhs.strip())
        return {}


class FakeSQS:
    """Minimal in-memory SQS client that satisfies the QueueClient interface."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deleted: list[str] = []

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> dict:
        import json
        msg_id = str(uuid.uuid4())
        self.messages.append({
            "MessageId": msg_id,
            "Body": MessageBody,
            "ReceiptHandle": f"rh-{msg_id}",
        })
        return {"MessageId": msg_id}

    def receive_message(self, *, QueueUrl: str, **kwargs) -> dict:
        if not self.messages:
            return {}
        msg = self.messages[0]
        return {"Messages": [{
            "Body": msg["Body"],
            "ReceiptHandle": msg["ReceiptHandle"],
            "Attributes": {"ApproximateReceiveCount": "1"},
        }]}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> dict:
        self.deleted.append(ReceiptHandle)
        self.messages = [m for m in self.messages if m["ReceiptHandle"] != ReceiptHandle]
        return {}


def _make_store_and_client():
    """Return (JobStore with FakeTable, QueueClient with FakeSQS)."""
    table = FakeTable()
    sqs = FakeSQS()
    store = JobStore(table=table)
    client = QueueClient(sqs_client=sqs, queue_url="fake://main")
    return store, client, table, sqs


def _make_job_record(job_id: str, league: LeagueAverages = _LEAGUE) -> dict:
    """Build a minimal QUEUED job record for direct insertion into a FakeTable."""
    return {
        "job_id": job_id,
        "status": "queued",
        "attempt_count": 0,
        "team_a_key": "key_a",
        "team_b_key": "key_b",
        "team_a_player_ids": [1, 2, 3, 4, 5],
        "team_b_player_ids": [6, 7, 8, 9, 10],
        "league": {k: float(v) for k, v in dataclasses.asdict(league).items()},
        "queued_at": "2026-06-17T00:00:00Z",
        "ttl": int(time.time()) + 86400,
    }


def _fake_fetch(players: list[dict], lineup: dict | None):
    """Return a fetch_lineup callable that always returns (players, lineup)."""
    def _fetch(player_ids, lineup_key):
        return players, lineup
    return _fetch


# ── Test 1: submit_job ────────────────────────────────────────────────────────


def test_submit_job_writes_queued_record_and_enqueues():
    """submit_job must write a QUEUED DynamoDB record and send exactly one SQS message."""
    store, client, table, sqs = _make_store_and_client()

    job_id = submit_job(
        team_a_key="lineup_key_a",
        team_a_player_ids=[100, 200, 300, 400, 500],
        team_b_key="lineup_key_b",
        team_b_player_ids=[600, 700, 800, 900, 1000],
        seed=42,
        league=_LEAGUE,
        job_store=store,
        queue_client=client,
    )

    assert isinstance(job_id, str) and len(job_id) == 36, "job_id must be a UUID4 string"

    # DynamoDB record
    job = store.get_job(job_id)
    assert job["status"] == "queued"
    assert job["attempt_count"] == 0
    assert job["team_a_key"] == "lineup_key_a"
    assert [int(x) for x in job["team_a_player_ids"]] == [100, 200, 300, 400, 500]
    assert int(job["seed"]) == 42
    assert "league" in job

    # SQS queue
    assert len(sqs.messages) == 1, "Exactly one SQS message must be enqueued"
    import json
    body = json.loads(sqs.messages[0]["Body"])
    assert body["job_id"] == job_id, "SQS message body must contain only job_id"


# ── Test 2: handle_job valid ──────────────────────────────────────────────────


def test_handle_job_valid_reaches_done():
    """
    handle_job on a QUEUED job must:
      - transition to DONE
      - write a result whose mean_margin matches a direct simulate() call with the same seed
    """
    store, client, table, sqs = _make_store_and_client()
    job_id = str(uuid.uuid4())
    players = _five_players()
    lineup = _sample_lineup()

    # Pre-insert a QUEUED job record with seed=77
    record = _make_job_record(job_id, league=_LEAGUE)
    record["seed"] = 77
    table.put_item(Item=record)
    # Also put a receipt handle into the fake SQS
    rh = f"rh-{job_id}"
    sqs.messages.append({"MessageId": "m1", "Body": f'{{"job_id": "{job_id}"}}', "ReceiptHandle": rh})

    status = handle_job(
        job_id, rh,
        queue_client=client,
        job_store=store,
        fetch_lineup=_fake_fetch(players, lineup),
    )

    assert status == "done", f"Expected 'done', got {status!r}"

    job = store.get_job(job_id)
    assert job["status"] == "done"
    assert "result" in job
    assert rh in sqs.deleted, "Message must be deleted on success"

    # The stored result must match a direct engine call with the same inputs and seed.
    direct = simulate(players, lineup, players, lineup, _LEAGUE, seed=77)
    stored_margin = job["result"]["mean_margin"]
    assert abs(stored_margin - direct.mean_margin) < 1e-6, (
        f"Stored margin {stored_margin:.4f} doesn't match direct call {direct.mean_margin:.4f}"
    )


# ── Test 3: handle_job on already-done job ────────────────────────────────────


def test_handle_job_done_skips_and_deletes():
    """
    handle_job on a job that is already DONE must delete the message and return
    'skipped' without re-running the engine. This is the idempotent duplicate-
    delivery guard.
    """
    store, client, table, sqs = _make_store_and_client()
    job_id = str(uuid.uuid4())

    record = _make_job_record(job_id)
    record["status"] = "done"  # already finished
    record["result"] = {"mean_margin": 3.5, "n_sims": 500}
    table.put_item(Item=record)

    rh = f"rh-{job_id}"
    sqs.messages.append({"MessageId": "m1", "Body": f'{{"job_id": "{job_id}"}}', "ReceiptHandle": rh})

    # Track whether fetch_lineup is ever called (it should NOT be for a done job)
    called = []
    def _sentinel_fetch(player_ids, lineup_key):
        called.append(True)
        return _five_players(), _sample_lineup()

    status = handle_job(
        job_id, rh,
        queue_client=client,
        job_store=store,
        fetch_lineup=_sentinel_fetch,
    )

    assert status == "skipped"
    assert rh in sqs.deleted, "Message must be deleted even for a skipped job"
    assert not called, "fetch_lineup must NOT be called when the job is already done"


# ── Test 4: handle_job with invalid lineup ─────────────────────────────────────


def test_handle_job_invalid_lineup_fails_fast():
    """
    When fetch_lineup raises LineupNotFoundError, handle_job must:
      - set status=FAILED with error_type=invalid_lineup
      - delete the SQS message (don't retry — the data won't appear on its own)
      - return 'failed'
    """
    store, client, table, sqs = _make_store_and_client()
    job_id = str(uuid.uuid4())

    record = _make_job_record(job_id)
    table.put_item(Item=record)

    rh = f"rh-{job_id}"
    sqs.messages.append({"MessageId": "m1", "Body": f'{{"job_id": "{job_id}"}}', "ReceiptHandle": rh})

    def _bad_fetch(player_ids, lineup_key):
        raise LineupNotFoundError("Players not found in my5-player-params: [9999]")

    status = handle_job(
        job_id, rh,
        queue_client=client,
        job_store=store,
        fetch_lineup=_bad_fetch,
    )

    assert status == "failed", f"Expected 'failed', got {status!r}"

    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error_type"] == "invalid_lineup"
    assert "9999" in job["error_message"]
    assert rh in sqs.deleted, "Message must be deleted on invalid_lineup to prevent useless retries"


# ── Test 5: simulated crash before delete ─────────────────────────────────────


def test_handle_job_crash_does_not_delete_message():
    """
    If the worker crashes (unexpected exception) after setting status=RUNNING but
    before calling queue_client.delete(), the SQS message must NOT be deleted.
    VisibilityTimeout will expire and SQS retries automatically.

    This is the core crash-recovery guarantee: we only delete after clean success
    or deliberate fail-fast. Any other exception propagates without deleting.
    """
    store, client, table, sqs = _make_store_and_client()
    job_id = str(uuid.uuid4())

    record = _make_job_record(job_id)
    table.put_item(Item=record)

    rh = f"rh-{job_id}"
    sqs.messages.append({"MessageId": "m1", "Body": f'{{"job_id": "{job_id}"}}', "ReceiptHandle": rh})

    def _crash_after_claim(player_ids, lineup_key):
        # Raises AFTER the job is claimed (status=RUNNING) but before simulate() returns.
        raise RuntimeError("Disk full — simulated crash")

    with pytest.raises(RuntimeError, match="Disk full"):
        handle_job(
            job_id, rh,
            queue_client=client,
            job_store=store,
            fetch_lineup=_crash_after_claim,
        )

    # The message must still be in the queue (NOT deleted).
    assert rh not in sqs.deleted, (
        "Message must NOT be deleted after a crash — VisibilityTimeout will retry"
    )
    # The job should still be RUNNING (the crash interrupted before write_result).
    job = store.get_job(job_id)
    assert job["status"] == "running", (
        f"Job status should be 'running' after crash, got {job['status']!r}"
    )
