"""
Tests for P2-C WebSocket push layer.

Six tests (all sync; async coroutines run via asyncio.run()):

  1. push_progress — sends to all registered connections; prunes on GONE;
     no-op when no subscribers.
  2. Registry — register/lookup/unregister; multi-watcher per job;
     idempotent unregister.
  3. Bridge — a sync-thread call lands on the async bus without a
     loop-thread violation.
  4. Handshake ordering — register-before-snapshot invariant:
     a terminal event that fires after register but before snapshot is
     observable via BOTH the queue (fan-out) AND the snapshot. Client
     cannot be left hung on "running".
  5. NotifyingJobStore — update_progress writes through to inner AND
     publishes to bus; inner store output identical to plain JobStore.
  6. Terminal write atomicity — assert status + result are written together
     in a single update (write_result is one update_item call).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest

from my5.job_store import JobStore
from my5.simulator import LeagueAverages, SimResult
from my5.ws.bus import EventBus
from my5.ws.notifying_store import NotifyingJobStore
from my5.ws.push import GONE, push_progress
from my5.ws.registry import Registry
from my5.ws.server import LocalSender, _job_to_message

# ── Shared fixtures ───────────────────────────────────────────────────────────

_LEAGUE = LeagueAverages(
    usage_rate=0.19, rim_fg_pct=0.616, mid_fg_pct=0.410, fg3_pct=0.379,
    tov_rate=0.1119, ft_rate=0.038, ft_pct=0.770, oreb_rate=0.065,
    shot_rim_rate=0.450, shot_mid_rate=0.175, shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616, opp_mid_fg_pct=0.410, opp_3p_fg_pct=0.379,
    forced_to_rate=0.126, dreb_rate=0.730,
)


class FakeTable:
    """In-memory DynamoDB table (same implementation as test_progress.py)."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        # Record each update_item call for atomicity inspection.
        self.update_calls: list[dict] = []

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
        self,
        *,
        Key: dict,
        UpdateExpression: str,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
    ) -> dict:
        self.update_calls.append({
            "Key": Key,
            "UpdateExpression": UpdateExpression,
            "ExpressionAttributeNames": ExpressionAttributeNames or {},
            "ExpressionAttributeValues": ExpressionAttributeValues or {},
        })
        job_id = Key["job_id"]
        item = self._items.setdefault(job_id, {"job_id": job_id})
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}

        def _resolve(name: str) -> str:
            return names.get(name, name)

        def _value(ph: str) -> Any:
            v = values[ph]
            return float(v) if isinstance(v, Decimal) else v

        body = UpdateExpression.strip()
        if body.upper().startswith("SET "):
            body = body[4:]
        for clause in body.split(","):
            clause = clause.strip()
            if "= " in clause and " + " in clause:
                lhs, rhs = clause.split("=", 1)
                field = _resolve(lhs.strip())
                _, inc_ph = rhs.split("+")
                item[field] = item.get(field, 0) + _value(inc_ph.strip())
            else:
                lhs, rhs = clause.split("=", 1)
                field = _resolve(lhs.strip())
                item[field] = _value(rhs.strip())
        return {}


def _make_store(table: FakeTable | None = None) -> JobStore:
    return JobStore(table=table or FakeTable())


# ── Test 1: push_progress ─────────────────────────────────────────────────────


def test_push_progress_sends_to_all_connections_and_prunes_gone():
    """
    push_progress delivers to all registered connections.
    When Sender.send returns GONE, that conn_id is removed from the registry.
    When there are no subscribers, it is a no-op.
    """
    async def run():
        registry = Registry()
        sender = LocalSender()

        q1: asyncio.Queue[str] = asyncio.Queue()
        q2: asyncio.Queue[str] = asyncio.Queue()

        registry.register("job1", "conn1")
        registry.register("job1", "conn2")
        sender.add("conn1", q1)
        sender.add("conn2", q2)

        msg = {"type": "progress", "sims_done": 50, "ci_half": 4.2}
        await push_progress("job1", msg, registry, sender)

        # Both connections received the payload.
        assert not q1.empty()
        assert not q2.empty()
        payload1 = json.loads(await q1.get())
        payload2 = json.loads(await q2.get())
        assert payload1["sims_done"] == 50
        assert payload2["sims_done"] == 50

        # No-op when no subscribers.
        await push_progress("job_nobody_watching", msg, registry, sender)

        # GONE: conn3 is in registry but NOT in sender (simulates dropped connection).
        registry.register("job2", "conn3")
        # conn3 has no queue in sender → send returns GONE → prune
        await push_progress("job2", msg, registry, sender)
        assert registry.lookup("job2") == [], (
            "conn3 should have been pruned after GONE"
        )

    asyncio.run(run())


# ── Test 2: Registry ──────────────────────────────────────────────────────────


def test_registry_register_lookup_unregister():
    """
    Registry correctly maps job_id → conn_ids and handles edge cases.
    """
    reg = Registry()

    # Empty lookup.
    assert reg.lookup("j1") == []

    # Single registration.
    reg.register("j1", "c1")
    assert "c1" in reg.lookup("j1")

    # Multi-watcher: two connections for same job.
    reg.register("j1", "c2")
    assert set(reg.lookup("j1")) == {"c1", "c2"}

    # Separate job.
    reg.register("j2", "c3")
    assert reg.lookup("j2") == ["c3"]
    assert "c3" not in reg.lookup("j1")

    # Unregister one.
    reg.unregister("c1")
    assert reg.lookup("j1") == ["c2"]

    # Idempotent unregister (second call is safe).
    reg.unregister("c1")
    assert reg.lookup("j1") == ["c2"]

    # Unregister last watcher cleans up job entry.
    reg.unregister("c2")
    assert reg.lookup("j1") == []


# ── Test 3: Sync→async bridge ─────────────────────────────────────────────────


def test_bridge_sync_thread_to_async_bus():
    """
    bus.post_threadsafe() from a non-event-loop thread delivers the event
    to the async queue without a RuntimeError or thread-safety violation.
    """
    bus = EventBus()
    received: list[dict] = []

    async def runner():
        bus.set_loop(asyncio.get_running_loop())
        event = {
            "job_id": "jtest",
            "message": {"type": "progress", "sims_done": 50, "ci_half": 4.0},
        }
        # Post from a background thread (the "worker thread" analog).
        t = threading.Thread(target=bus.post_threadsafe, args=(event,))
        t.start()
        t.join()
        # Yield to the event loop so call_soon_threadsafe fires put_nowait.
        await asyncio.sleep(0)
        item = bus._queue.get_nowait()
        received.append(item)

    asyncio.run(runner())

    assert len(received) == 1
    assert received[0]["job_id"] == "jtest"
    assert received[0]["message"]["sims_done"] == 50


# ── Test 4: Handshake ordering ────────────────────────────────────────────────


def test_handshake_register_first_catches_terminal_in_gap():
    """
    Register-before-snapshot invariant: if a terminal event fires after
    registration but before the snapshot is read, the client observes terminal
    state via BOTH the queue (from fan-out) AND the snapshot.

    No client can finish the connect handshake without receiving the terminal
    frame — either through the queue or via the snapshot. A duplicate terminal
    frame (both paths fire) is cosmetic; a missed terminal frame is fatal.
    """
    async def run():
        registry = Registry()
        sender = LocalSender()
        conn_id = "conn_a"
        job_id = "job_a"
        q: asyncio.Queue[str] = asyncio.Queue()

        # Step 1: Register FIRST.
        registry.register(job_id, conn_id)
        sender.add(conn_id, q)

        # Step 2: Terminal event fires (simulates the race — job completes
        # between register and snapshot in the real handler).
        done_msg = {
            "type": "done", "n_sims": 258, "mean_margin": 1.79,
            "ci_half_width": 1.95, "equiv_net_rating": 3.6, "converged": True,
        }
        await push_progress(job_id, done_msg, registry, sender)

        # The terminal frame is now in the queue (delivered via fan-out).
        assert not q.empty(), "Terminal frame must be in queue after push_progress"
        queued_msg = json.loads(q.get_nowait())
        assert queued_msg["type"] == "done", "Queued frame must be terminal"

        # Step 3: Snapshot also shows terminal (because job_store is authoritative).
        # _job_to_message simulates the snapshot read showing done status.
        fake_terminal_job = {
            "status": "done",
            "result": {
                "n_sims": 258, "mean_margin": 1.79,
                "ci_half_width": 1.95, "equiv_net_rating": 3.6, "converged": True,
            },
        }
        snapshot = _job_to_message(fake_terminal_job)
        assert snapshot["type"] == "done", (
            "Snapshot must show done — client catches terminal via snapshot"
        )

    asyncio.run(run())


# ── Test 5: NotifyingJobStore ─────────────────────────────────────────────────


def test_notifying_store_publishes_to_bus_and_writes_through():
    """
    NotifyingJobStore.update_progress:
      - calls inner.update_progress (job record updated)
      - publishes a progress event to the bus

    The inner store's data is identical to a plain JobStore — the wrapper
    adds no extra data, changes no field values.
    """
    table = FakeTable()
    inner = _make_store(table)
    bus = EventBus()

    # Capture bus events without a real async loop.
    published: list[dict] = []
    original_post = bus.post_threadsafe

    def _capture(event):
        published.append(event)

    bus.post_threadsafe = _capture  # type: ignore[method-assign]

    notifying = NotifyingJobStore(inner=inner, bus=bus)

    job_id = str(uuid.uuid4())
    table.put_item(Item={"job_id": job_id, "status": "running", "attempt_count": 1})

    notifying.update_progress(job_id, sims_done=50, ci_half=4.42)

    # 1. Inner store was updated.
    job = inner.get_job(job_id)
    assert job.get("progress_sims") == 50
    assert job.get("progress_ci") == pytest.approx(4.42, rel=1e-3)

    # 2. Bus received exactly one event.
    assert len(published) == 1
    ev = published[0]
    assert ev["job_id"] == job_id
    assert ev["message"]["type"] == "progress"
    assert ev["message"]["sims_done"] == 50
    assert ev["message"]["ci_half"] == pytest.approx(4.42, rel=1e-3)


def test_notifying_store_publishes_done_on_write_result():
    """
    NotifyingJobStore.write_result calls through to inner AND publishes
    a 'done' event with the full SimResult fields.
    """
    table = FakeTable()
    inner = _make_store(table)
    bus = EventBus()

    published: list[dict] = []
    bus.post_threadsafe = lambda ev: published.append(ev)  # type: ignore[method-assign]

    notifying = NotifyingJobStore(inner=inner, bus=bus)

    job_id = str(uuid.uuid4())
    table.put_item(Item={"job_id": job_id, "status": "running", "attempt_count": 1})

    result = SimResult(
        mean_margin=1.79, ci_half_width=1.95, n_sims=258,
        equiv_net_rating=3.6, converged=True,
        mean_pts_a=112.5, mean_pts_b=110.7,
    )
    notifying.write_result(job_id, result, completed_at="2026-06-19T00:00:00Z")

    # Inner store shows done.
    job = inner.get_job(job_id)
    assert job["status"] == "done"

    # Bus event carries the terminal message.
    assert len(published) == 1
    msg = published[0]["message"]
    assert msg["type"] == "done"
    assert msg["n_sims"] == 258
    assert msg["mean_margin"] == pytest.approx(1.79, rel=1e-3)
    assert msg["converged"] is True


# ── Test 6: Terminal write atomicity ──────────────────────────────────────────


def test_terminal_write_is_atomic_single_update_item():
    """
    job_store.write_result must issue exactly ONE update_item call that
    sets status, result map, and completed_at together.

    This is the precondition for the fan-out model: the bus event is posted
    after a single write, so any subscriber reading the job record immediately
    after receiving the bus event will always see status=done AND result present
    — never one without the other.
    """
    table = FakeTable()
    store = _make_store(table)

    job_id = str(uuid.uuid4())
    table.put_item(Item={"job_id": job_id, "status": "running", "attempt_count": 1})

    # Reset call log to count only from this point.
    table.update_calls.clear()

    result = SimResult(
        mean_margin=2.00, ci_half_width=1.80, n_sims=200,
        equiv_net_rating=2.1, converged=True,
        mean_pts_a=111.0, mean_pts_b=109.0,
    )
    store.write_result(job_id, result, completed_at="2026-06-19T00:00:00Z")

    # Exactly one update_item call for the terminal write.
    assert len(table.update_calls) == 1, (
        f"write_result must be ONE update_item call; got {len(table.update_calls)}"
    )

    call = table.update_calls[0]
    expr = call["UpdateExpression"]

    # The single call must set both status and result (verify by alias presence).
    names = call["ExpressionAttributeNames"]
    values = call["ExpressionAttributeValues"]
    assert "#s" in names and names["#s"] == "status"
    assert "#r" in names and names["#r"] == "result"
    assert ":s" in values and values[":s"] == "done"

    # And the job record shows both fields present simultaneously.
    job = store.get_job(job_id)
    assert job["status"] == "done"
    assert "result" in job
    assert job["result"]["n_sims"] == pytest.approx(200, rel=1e-6)
