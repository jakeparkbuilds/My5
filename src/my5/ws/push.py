"""
Portable push_progress core — byte-identical local and on Lambda.

The only part that changes between local and AWS is the Sender implementation:
  Local: LocalSender (asyncio.Queue per connection, defined in server.py)
  AWS:   ApigwSender (post_to_connection on the APIGW Management API)

This module knows about neither.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


# Sentinel returned by Sender.send when a connection is dead.
# Local: conn_id not found in LocalSender's queue map (connection closed).
# AWS:   post_to_connection raised GoneException (HTTP 410).
class _GoneSentinel:
    """Singleton sentinel for dead connections."""
    _instance: "_GoneSentinel | None" = None

    def __new__(cls) -> "_GoneSentinel":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "GONE"


GONE: _GoneSentinel = _GoneSentinel()


@runtime_checkable
class Sender(Protocol):
    """
    Delivery abstraction. send() returns GONE for dead connections.

    Local:  LocalSender.send puts payload on the connection's asyncio.Queue.
    AWS:    ApigwSender.send calls post_to_connection; returns GONE on 410.
    """

    async def send(self, conn_id: str, payload: str) -> _GoneSentinel | None: ...


async def push_progress(
    job_id: str,
    message: dict[str, Any],
    registry: Any,
    sender: Sender,
) -> None:
    """
    Fan-out one message to all connections registered for job_id.

    Prunes dead connections (GONE) from the registry on first send failure.
    No-op when there are no registered connections.

    Called by the bus consumer task on each event from the worker.
    Identical on local (asyncio.Queue delivery) and AWS (post_to_connection).
    """
    conn_ids = registry.lookup(job_id)
    if not conn_ids:
        return
    payload = json.dumps(message)
    for conn_id in conn_ids:
        result = await sender.send(conn_id, payload)
        if result is GONE:
            registry.unregister(conn_id)
