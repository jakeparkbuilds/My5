"""
In-memory connection registry: job_id → set[connection_id].

Local stand-in for the AWS my5-ws-connections DynamoDB table.
AWS port: swap this class for one backed by DynamoDB without changing any caller.
"""
from __future__ import annotations

import threading


class Registry:
    """
    Thread-safe in-memory registry mapping job_id → set[conn_id].

    Interface:
        register(job_id, conn_id)   — called by ws_handler at connect
        lookup(job_id) → list[str]  — called by push_progress per fan-out event
        unregister(conn_id)         — called by ws_handler at disconnect (finally)

    Multiple clients may watch the same job_id; one client may not watch multiple
    jobs (each WS connection is bound to one job_id at connect time via URL path).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job_to_conns: dict[str, set[str]] = {}
        self._conn_to_job: dict[str, str] = {}

    def register(self, job_id: str, conn_id: str) -> None:
        with self._lock:
            self._job_to_conns.setdefault(job_id, set()).add(conn_id)
            self._conn_to_job[conn_id] = job_id

    def lookup(self, job_id: str) -> list[str]:
        with self._lock:
            return list(self._job_to_conns.get(job_id, set()))

    def unregister(self, conn_id: str) -> None:
        """Idempotent: calling twice for the same conn_id is safe."""
        with self._lock:
            job_id = self._conn_to_job.pop(conn_id, None)
            if job_id is not None:
                bucket = self._job_to_conns.get(job_id, set())
                bucket.discard(conn_id)
                if not bucket:
                    self._job_to_conns.pop(job_id, None)
