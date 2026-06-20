"""
End-to-end proof for P2-C: WebSocket live progress push.

No Docker required — uses an in-memory FakeTable-backed JobStore.

What this proves:
  1. FastAPI WS server starts, bus consumer runs.
  2. Client connects → receives snapshot (sims_done=0, job not started yet).
  3. Client registers BEFORE snapshot is sent (handled inside ws_handler).
  4. Worker thread starts after client is connected (first snapshot received).
  5. Worker emits progress via NotifyingJobStore → bus → push_progress →
     LocalSender → connection's asyncio.Queue → websocket.send_text.
  6. Client receives a climbing sequence of progress frames.
  7. Client receives exactly one `done` terminal frame.
  8. Terminal mean_margin equals a direct simulate(seed=same) call (belt-and-suspenders).

Run:
    .venv/bin/python3 scripts/e2e_ws.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import uvicorn
import websockets

from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, simulate
from my5.ws.bus import EventBus
from my5.ws.notifying_store import NotifyingJobStore
from my5.ws.registry import Registry
from my5.ws.server import LocalSender, create_app

# ── Config ────────────────────────────────────────────────────────────────────

_PORT = 8791
_SEED = 42

_LEAGUE = LeagueAverages(
    usage_rate=0.19, rim_fg_pct=0.616, mid_fg_pct=0.410, fg3_pct=0.379,
    tov_rate=0.1119, ft_rate=0.038, ft_pct=0.770, oreb_rate=0.065,
    shot_rim_rate=0.450, shot_mid_rate=0.175, shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616, opp_mid_fg_pct=0.410, opp_3p_fg_pct=0.379,
    forced_to_rate=0.126, dreb_rate=0.730,
)

# ── In-memory fake table ──────────────────────────────────────────────────────


class _FakeTable:
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
        self,
        *,
        Key: dict,
        UpdateExpression: str,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
    ) -> dict:
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


class _FakeQueueClient:
    """Minimal QueueClient stub — handle_job only needs delete()."""
    def delete(self, receipt_handle: str, *, queue_url: str | None = None) -> None:
        pass


# ── Fixture builders ──────────────────────────────────────────────────────────


def _make_player(**overrides: Any) -> dict:
    base = dict(
        usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
        shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
        rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
    )
    base.update(overrides)
    return base


def _make_job_record(job_id: str, seed: int) -> dict:
    return {
        "job_id": job_id,
        "status": "queued",
        "attempt_count": 0,
        "team_a_key": "key_a",
        "team_b_key": "key_b",
        "team_a_player_ids": [1, 2, 3, 4, 5],
        "team_b_player_ids": [6, 7, 8, 9, 10],
        "league": {k: float(v) for k, v in dataclasses.asdict(_LEAGUE).items()},
        "seed": seed,
        "queued_at": "2026-06-19T00:00:00Z",
        "ttl": int(time.time()) + 86400,
    }


# ── Server startup ────────────────────────────────────────────────────────────


def _start_server(app, port: int) -> uvicorn.Server:
    """Start uvicorn in a daemon thread; return the Server object."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    # Poll until server.started (set by uvicorn after lifespan startup completes).
    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn failed to start within 10s")
    return server


# ── Main e2e flow ─────────────────────────────────────────────────────────────


async def main() -> None:
    print("=" * 60)
    print("  My5 P2-C End-to-End WebSocket Proof")
    print("  (in-memory, no Docker required)")
    print("=" * 60)

    # ── 1. Wire up components ─────────────────────────────────────────────────
    fake_table = _FakeTable()
    inner_store = JobStore(table=fake_table)
    bus = EventBus()
    notifying_store = NotifyingJobStore(inner=inner_store, bus=bus)
    registry = Registry()
    sender = LocalSender()

    app = create_app(notifying_store, registry, bus, sender)

    # ── 2. Start server ───────────────────────────────────────────────────────
    print("\n[setup] Starting FastAPI WS server on port", _PORT)
    server = _start_server(app, _PORT)
    print("[setup] Server ready ✓")

    # ── 3. Create job record ──────────────────────────────────────────────────
    players = [_make_player() for _ in range(5)]
    lineup = {
        "lineup_key": "e2e_key",
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to": 12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55, "dreb_opp": 75, "dreb_rate": 0.733,
    }

    job_id = str(uuid.uuid4())
    inner_store.put_job(_make_job_record(job_id, _SEED))
    print(f"[setup] Job {job_id[:8]}... written to store")

    # ── 4. Collect frames via WS client ───────────────────────────────────────
    frames: list[dict] = []
    worker_started = threading.Event()

    def _run_worker() -> None:
        worker_started.wait(timeout=5.0)
        handle_job(
            job_id, "fake-receipt",
            queue_client=_FakeQueueClient(),
            job_store=notifying_store,
            fetch_lineup=lambda pids, key: (players, lineup),
        )

    worker_thread = threading.Thread(target=_run_worker, daemon=True)
    worker_thread.start()

    print(f"[client] Connecting to ws://127.0.0.1:{_PORT}/ws/jobs/{job_id[:8]}...")
    first_frame_received = False

    async with websockets.connect(
        f"ws://127.0.0.1:{_PORT}/ws/jobs/{job_id}"
    ) as ws:
        async for raw in ws:
            frame = json.loads(raw)
            frames.append(frame)

            if not first_frame_received:
                # First frame is the snapshot. Signal worker to start.
                first_frame_received = True
                worker_started.set()

            if frame["type"] == "progress" and frame["sims_done"] > 0:
                print(f"  progress: {frame['sims_done']:>5} sims  CI ±{frame['ci_half']:.2f} pts")
            elif frame["type"] == "progress" and frame["sims_done"] == 0:
                print("  (snapshot: job queued, waiting for worker...)")

            if frame["type"] in ("done", "failed"):
                break

    worker_thread.join(timeout=30.0)

    # ── 5. Report + assertions ────────────────────────────────────────────────
    print()

    terminal = frames[-1]
    assert terminal["type"] == "done", f"Expected terminal 'done', got {terminal}"

    progress_frames = [f for f in frames if f["type"] == "progress" and f["sims_done"] > 0]
    done_frames = [f for f in frames if f["type"] == "done"]

    assert len(done_frames) == 1, f"Expected exactly 1 done frame, got {len(done_frames)}"
    assert len(progress_frames) >= 1, "Expected at least 1 progress frame"

    # Frames must be monotonically increasing in sims_done.
    sims_sequence = [f["sims_done"] for f in progress_frames]
    assert sims_sequence == sorted(sims_sequence), (
        f"progress sims_done not monotone: {sims_sequence}"
    )

    print(f"  DONE after {terminal['n_sims']} sims")
    print(f"    mean_margin      = {terminal['mean_margin']:+.3f} pts")
    print(f"    ci_half_width    = {terminal['ci_half_width']:.3f} pts")
    print(f"    equiv_net_rating = {terminal['equiv_net_rating']:+.1f} pts/100")
    print(f"    converged        = {terminal['converged']}")

    # ── 6. Belt-and-suspenders: stack result == direct call ───────────────────
    direct = simulate(players, lineup, players, lineup, _LEAGUE, seed=_SEED)
    delta = abs(terminal["mean_margin"] - direct.mean_margin)
    assert delta < 1e-6, (
        f"Stack margin {terminal['mean_margin']:.6f} ≠ direct {direct.mean_margin:.6f}"
    )
    print(f"\n  Direct simulate(seed={_SEED}) margin = {direct.mean_margin:+.3f} pts")
    print(f"  Stack vs direct delta  = {delta:.2e}  ✓")

    # ── 7. Frame sequence summary ─────────────────────────────────────────────
    print(f"\n  Total frames received : {len(frames)}")
    print(f"  Progress frames       : {len(progress_frames)}")
    print(f"  Terminal frames       : {len(done_frames)}")
    print(f"  sims_done sequence    : {sims_sequence}")

    print("\n" + "=" * 60)
    print("  P2-C E2E PASSED ✓")
    print("=" * 60)

    # Shutdown server
    server.should_exit = True


if __name__ == "__main__":
    asyncio.run(main())
