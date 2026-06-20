"""
Async event bus + sync→async bridge for local progress fan-out.

Architecture:
  Worker thread (sync)    →  post_threadsafe(event)
                          →  loop.call_soon_threadsafe(queue.put_nowait, event)
  Server event loop       →  consume() reads queue, calls push_fn per event

The bridge (call_soon_threadsafe) is the named seam: it is the only place
where the sync worker world and the async server world meet. Tests exercise
it directly (see test_ws.py:test_bridge_sync_thread_to_async_bus).

On AWS this whole file is replaced by DynamoDB Streams → fan-out Lambda.
The bus is local-only infrastructure; the push_progress core it calls is portable.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class EventBus:
    """
    In-process event bus for simulation progress snapshots.

    Usage:
        bus = EventBus()

        # At server startup (inside an async context):
        bus.set_loop(asyncio.get_running_loop())
        asyncio.create_task(bus.consume(push_fn))

        # From any worker thread:
        bus.post_threadsafe({"job_id": "...", "message": {...}})
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Capture the running event loop. Call once at server startup from
        inside an async context (asyncio.get_running_loop() returns the
        server's loop). Must be called before any post_threadsafe calls.
        """
        self._loop = loop
        self._queue = asyncio.Queue()

    def post_threadsafe(self, event: dict[str, Any]) -> None:
        """
        Sync→async bridge: schedule event delivery from any thread.

        Uses loop.call_soon_threadsafe so the queue.put_nowait runs on
        the server's event loop thread — never blocking the worker thread.
        Safe to call before set_loop (event silently dropped; shouldn't
        happen in practice since server starts before workers).
        """
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def consume(
        self,
        push_fn: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        Long-running consumer task. Reads events and calls push_fn for each.

        push_fn(job_id, message) is async — typically push_progress bound
        to the current registry and sender.

        Runs until cancelled (at server shutdown via lifespan cleanup).
        """
        assert self._queue is not None, "call set_loop() before consume()"
        while True:
            event = await self._queue.get()
            await push_fn(event["job_id"], event["message"])
