"""
FastAPI WebSocket server for live simulation progress (local target).

Route: GET /ws/jobs/{job_id}  (WebSocket upgrade)

Connection lifecycle:
  1. accept() — WS handshake
  2. REGISTER connection in registry FIRST (correctness-critical ordering)
  3. Read job snapshot from store → send one synthetic frame of current state
     (late-joiner recovery: client gets where we are right now)
  4. If already terminal (done/failed): return immediately
  5. Drain per-connection asyncio.Queue until a terminal frame arrives
     (frames arrive via bus → push_progress → LocalSender.send → queue)
  6. finally: unregister from registry + sender (handles both clean disconnect
     and WebSocketDisconnect exception)

Register-then-snapshot ordering rationale (DECISIONS.md P2-C):
  If the job goes terminal between register (step 2) and snapshot (step 3),
  the bus will push the terminal frame to our queue AND the snapshot will also
  show terminal status. Client gets terminal either way. A duplicate terminal
  frame is cosmetic; a missed terminal frame would leave the client hung.
  Snapshot-then-register could miss terminal events in the gap.

LocalSender is the local twin of AWS post_to_connection (APIGW Management API).
The seam: swap LocalSender for ApigwSender without changing push_progress.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from my5.ws.push import GONE, push_progress


# ── Local Sender ─────────────────────────────────────────────────────────────


class LocalSender:
    """
    Local Sender: conn_id → asyncio.Queue.

    AWS twin: post_to_connection on the APIGW Management API.
    Seam: this class is replaced for the AWS shell; push_progress is unchanged.

    send() returns GONE when conn_id is not registered (connection closed
    without a proper disconnect event — the prune-on-send path).
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str]] = {}

    def add(self, conn_id: str, queue: asyncio.Queue[str]) -> None:
        self._queues[conn_id] = queue

    def remove(self, conn_id: str) -> None:
        self._queues.pop(conn_id, None)

    async def send(self, conn_id: str, payload: str) -> Any:
        q = self._queues.get(conn_id)
        if q is None:
            return GONE
        await q.put(payload)
        return None


# ── Message helpers ───────────────────────────────────────────────────────────


def _job_to_message(job: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a job record to a WebSocket message.

    Used for the initial snapshot on connect (late-joiner recovery).
    The message type determines whether the client should wait for more frames.
    """
    status = job.get("status", "queued")

    if status == "done":
        r = job["result"]
        return {
            "type": "done",
            "n_sims": int(r["n_sims"]),
            "mean_margin": float(r["mean_margin"]),
            "ci_half_width": float(r["ci_half_width"]),
            "equiv_net_rating": float(r["equiv_net_rating"]),
            "converged": bool(r["converged"]),
        }

    if status == "failed":
        return {
            "type": "failed",
            "error_type": job.get("error_type", "unknown"),
            "error_message": job.get("error_message", ""),
        }

    # queued or running — send latest progress snapshot (sims_done=0 if not started)
    sims = job.get("progress_sims")
    ci = job.get("progress_ci")
    return {
        "type": "progress",
        "sims_done": int(sims) if sims is not None else 0,
        "ci_half": float(ci) if ci is not None else 0.0,
    }


# ── App factory ───────────────────────────────────────────────────────────────


def create_app(
    job_store: Any,
    registry: Any,
    bus: Any,
    sender: LocalSender,
) -> FastAPI:
    """
    Build and return the FastAPI app with injected dependencies.

    Use a factory (not module-level singletons) so tests and e2e scripts
    can inject their own fakes — same pattern as JobStore and QueueClient.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Capture the server's event loop so worker threads can call
        # bus.post_threadsafe() from the sync world.
        bus.set_loop(asyncio.get_running_loop())

        async def _push_fn(job_id: str, message: dict[str, Any]) -> None:
            await push_progress(job_id, message, registry, sender)

        task = asyncio.create_task(bus.consume(_push_fn))
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws/jobs/{job_id}")
    async def ws_handler(websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()

        conn_id = str(uuid.uuid4())
        queue: asyncio.Queue[str] = asyncio.Queue()

        # Step 2: REGISTER FIRST — before reading snapshot.
        # Rationale: if job goes terminal between register and snapshot, the
        # bus delivers the terminal frame to our queue AND the snapshot shows
        # terminal. Either path delivers terminal; neither can be missed.
        registry.register(job_id, conn_id)
        sender.add(conn_id, queue)

        try:
            # Step 3: Snapshot — late-joiner recovery.
            try:
                job = job_store.get_job(job_id)
            except KeyError:
                await websocket.send_text(json.dumps({
                    "type": "failed",
                    "error_type": "job_not_found",
                    "error_message": f"Job {job_id!r} not found.",
                }))
                return

            snapshot = _job_to_message(job)
            await websocket.send_text(json.dumps(snapshot))

            # Step 4: Already terminal → done; no queue needed.
            if snapshot["type"] in ("done", "failed"):
                return

            # Step 5: Drain queue until terminal frame.
            while True:
                try:
                    msg_str = await asyncio.wait_for(queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    # Safety net: re-read job state after 5 min silence.
                    try:
                        job = job_store.get_job(job_id)
                    except KeyError:
                        break
                    snapshot = _job_to_message(job)
                    await websocket.send_text(json.dumps(snapshot))
                    if snapshot["type"] in ("done", "failed"):
                        break
                    continue

                await websocket.send_text(msg_str)
                data = json.loads(msg_str)
                if data["type"] in ("done", "failed"):
                    break

        except WebSocketDisconnect:
            pass
        finally:
            # Step 6: Cleanup regardless of how we exit.
            registry.unregister(conn_id)
            sender.remove(conn_id)

    return app
