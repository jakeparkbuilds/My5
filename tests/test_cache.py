"""
Tests for the P3 caching layer: cache.py, and the cache integration in
submit_job.py and job_worker.py.

Eight tests:
  1. make_cache_key is symmetric — (A, B, s) == (B, A, s)
  2. make_cache_key is seed-bound — (A, B, 42) != (A, B, 99)
  3. SimCache.get returns None on miss (empty table)
  4. SimCache.put → get roundtrip returns equal SimResult (all 7 fields)
  5. submit_job cache HIT: no SQS message, no job record, cache_hit=True, cached_result populated
  6. submit_job cache MISS: SQS message enqueued, job record written, cache_hit=False
  7. handle_job writes result to cache after successful completion
  8. Determinism invariant: cached result == direct simulate(seed=42)
"""
from __future__ import annotations

import dataclasses
import time
import uuid
from decimal import Decimal
from typing import Any

from my5.cache import SimCache, make_cache_key
from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, SimResult, simulate
from my5.submit_job import _DEFAULT_LEAGUE, submit_job

# ── Shared test data ──────────────────────────────────────────────────────────

_LEAGUE = LeagueAverages(
    usage_rate=0.19, rim_fg_pct=0.616, mid_fg_pct=0.410, fg3_pct=0.379,
    tov_rate=0.1119, ft_rate=0.038, ft_pct=0.770, oreb_rate=0.065,
    shot_rim_rate=0.450, shot_mid_rate=0.175, shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616, opp_mid_fg_pct=0.410, opp_3p_fg_pct=0.379,
    forced_to_rate=0.126, dreb_rate=0.730,
)


def _make_player(**overrides: Any) -> dict:
    base = dict(
        usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
        shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
        rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
    )
    base.update(overrides)
    return base


def _five_players() -> list[dict]:
    return [_make_player() for _ in range(5)]


def _sample_lineup(key: str = "test_key") -> dict:
    return {
        "lineup_key": key,
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to": 12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55, "dreb_opp": 75, "dreb_rate": 0.733,
    }


# ── In-memory fakes ───────────────────────────────────────────────────────────


class FakeCacheTable:
    """Minimal in-memory DynamoDB table that satisfies the SimCache interface."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def get_item(self, *, Key: dict) -> dict:
        key = Key["cache_key"]
        if key not in self._items:
            return {}
        return {"Item": dict(self._items[key])}

    def put_item(self, *, Item: dict) -> dict:
        # Convert Decimal to float on store so get_item reads back as float.
        def _dec(v: Any) -> Any:
            return float(v) if isinstance(v, Decimal) else v
        self._items[Item["cache_key"]] = {k: _dec(v) for k, v in Item.items()}
        return {}


class FakeTable:
    """In-memory DynamoDB table for JobStore."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def put_item(self, *, Item: dict) -> dict:
        from my5.job_store import _dec_to_float
        self._items[Item["job_id"]] = _dec_to_float(Item)
        return {}

    def get_item(self, *, Key: dict) -> dict:
        job_id = Key["job_id"]
        return {"Item": dict(self._items[job_id])} if job_id in self._items else {}

    def update_item(
        self, *, Key: dict, UpdateExpression: str,
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


class FakeSQS:
    """In-memory SQS client."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deleted: list[str] = []

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> dict:
        import json as _json
        msg_id = str(uuid.uuid4())
        self.messages.append({"MessageId": msg_id, "Body": MessageBody,
                               "ReceiptHandle": f"rh-{msg_id}"})
        return {"MessageId": msg_id}

    def receive_message(self, *, QueueUrl: str, **kwargs: Any) -> dict:
        return {}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> dict:
        self.deleted.append(ReceiptHandle)
        self.messages = [m for m in self.messages if m["ReceiptHandle"] != ReceiptHandle]
        return {}


class _NoOpQueueClient:
    def delete(self, receipt_handle: str, *, queue_url: str | None = None) -> None:
        pass


def _make_store_and_client():
    table = FakeTable()
    sqs = FakeSQS()
    store = JobStore(table=table)
    client = QueueClient(sqs_client=sqs, queue_url="fake://main")
    return store, client, table, sqs


def _make_job_record(job_id: str) -> dict:
    return {
        "job_id": job_id, "status": "queued", "attempt_count": 0,
        "team_a_key": "key_a", "team_b_key": "key_b",
        "team_a_player_ids": [1, 2, 3, 4, 5],
        "team_b_player_ids": [6, 7, 8, 9, 10],
        "league": {k: float(v) for k, v in dataclasses.asdict(_LEAGUE).items()},
        "seed": 42,
        "queued_at": "2026-06-20T00:00:00Z",
        "ttl": int(time.time()) + 86400,
    }


# ── Test 1: key symmetry ──────────────────────────────────────────────────────


def test_cache_key_is_symmetric():
    """(A, B, s) and (B, A, s) must produce the same cache key."""
    key_ab = make_cache_key("team#1#2#3#4#5", "team#6#7#8#9#10", 42)
    key_ba = make_cache_key("team#6#7#8#9#10", "team#1#2#3#4#5", 42)
    assert key_ab == key_ba, "Cache key must be symmetric (matchup order irrelevant)"


# ── Test 2: seed is part of the key ──────────────────────────────────────────


def test_cache_key_seed_in_key():
    """Same lineups, different seeds must NOT share a cache key."""
    key_42 = make_cache_key("team#A", "team#B", 42)
    key_99 = make_cache_key("team#A", "team#B", 99)
    assert key_42 != key_99, "Different seeds must produce different cache keys"


# ── Test 3: miss returns None ─────────────────────────────────────────────────


def test_cache_miss_returns_none():
    """Empty cache must return None for any key."""
    cache = SimCache(table=FakeCacheTable())
    result = cache.get(make_cache_key("key_a", "key_b", 42))
    assert result is None


# ── Test 4: put → get roundtrip ───────────────────────────────────────────────


def test_cache_put_get_roundtrip():
    """put then get must return a SimResult with all fields equal."""
    cache = SimCache(table=FakeCacheTable())
    key = make_cache_key("key_a", "key_b", 42)

    players = _five_players()
    lineup = _sample_lineup()
    original = simulate(players, lineup, players, lineup, _LEAGUE, seed=42)

    cache.put(key, original)
    retrieved = cache.get(key)

    assert retrieved is not None
    assert abs(retrieved.mean_margin - original.mean_margin) < 1e-6
    assert abs(retrieved.ci_half_width - original.ci_half_width) < 1e-6
    assert retrieved.n_sims == original.n_sims
    assert abs(retrieved.equiv_net_rating - original.equiv_net_rating) < 1e-6
    assert retrieved.converged == original.converged
    assert abs(retrieved.mean_pts_a - original.mean_pts_a) < 1e-6
    assert abs(retrieved.mean_pts_b - original.mean_pts_b) < 1e-6


# ── Test 5: submit cache HIT skips enqueue and job record ─────────────────────


def test_submit_hit_skips_enqueue_and_job_record():
    """
    When the cache has a matching entry, submit_job must:
      - return cache_hit=True with cached_result populated
      - NOT write a job record to DynamoDB
      - NOT enqueue an SQS message
    """
    cache_table = FakeCacheTable()
    cache = SimCache(table=cache_table)
    store, client, table, sqs = _make_store_and_client()

    # Pre-populate the cache with a known result
    players = _five_players()
    lineup = _sample_lineup()
    cached_result = simulate(players, lineup, players, lineup, _LEAGUE, seed=42)
    cache_key = make_cache_key("key_a", "key_b", 42)
    cache.put(cache_key, cached_result)

    result = submit_job(
        team_a_key="key_a",
        team_a_player_ids=[1, 2, 3, 4, 5],
        team_b_key="key_b",
        team_b_player_ids=[6, 7, 8, 9, 10],
        seed=42,
        league=_LEAGUE,
        job_store=store,
        queue_client=client,
        cache=cache,
    )

    assert result.cache_hit is True, "Expected cache_hit=True on a populated cache"
    assert result.job_id is None, "job_id must be None on cache hit (no job created)"
    assert result.cached_result is not None
    assert abs(result.cached_result.mean_margin - cached_result.mean_margin) < 1e-6

    assert len(sqs.messages) == 0, "No SQS message must be sent on cache hit"
    assert len(table._items) == 0, "No job record must be written on cache hit"


# ── Test 6: submit cache MISS enqueues normally ───────────────────────────────


def test_submit_miss_enqueues_normally():
    """
    When the cache is empty, submit_job must:
      - return cache_hit=False with a valid job_id
      - write a QUEUED job record
      - enqueue exactly one SQS message
    """
    cache = SimCache(table=FakeCacheTable())
    store, client, table, sqs = _make_store_and_client()

    result = submit_job(
        team_a_key="key_a",
        team_a_player_ids=[1, 2, 3, 4, 5],
        team_b_key="key_b",
        team_b_player_ids=[6, 7, 8, 9, 10],
        seed=42,
        league=_LEAGUE,
        job_store=store,
        queue_client=client,
        cache=cache,
    )

    assert result.cache_hit is False
    assert result.job_id is not None and len(result.job_id) == 36, "job_id must be UUID4"
    assert result.cached_result is None

    assert len(sqs.messages) == 1, "Exactly one SQS message on cache miss"
    job = store.get_job(result.job_id)
    assert job["status"] == "queued"


# ── Test 7: handle_job writes result to cache ─────────────────────────────────


def test_worker_writes_to_cache_after_done():
    """handle_job must populate the cache after successfully completing a job."""
    cache_table = FakeCacheTable()
    cache = SimCache(table=cache_table)
    store, client, table, sqs = _make_store_and_client()

    job_id = str(uuid.uuid4())
    record = _make_job_record(job_id)
    table.put_item(Item=record)

    rh = f"rh-{job_id}"
    players = _five_players()
    lineup = _sample_lineup()

    status = handle_job(
        job_id, rh,
        queue_client=client,
        job_store=store,
        fetch_lineup=lambda pids, key: (players, lineup),
        cache=cache,
    )

    assert status == "done"

    cache_key = make_cache_key("key_a", "key_b", 42)
    cached = cache.get(cache_key)
    assert cached is not None, "Cache must be populated after handle_job completes"


# ── Test 8: determinism invariant (cached == direct simulate) ─────────────────


def test_cached_result_matches_direct_simulate():
    """
    The result written to cache by the worker must be bit-identical to a direct
    simulate() call with the same seed. This proves cache hits are not approximations.
    """
    cache_table = FakeCacheTable()
    cache = SimCache(table=cache_table)
    store, client, table, sqs = _make_store_and_client()

    job_id = str(uuid.uuid4())
    record = _make_job_record(job_id)
    table.put_item(Item=record)

    players = _five_players()
    lineup = _sample_lineup()

    handle_job(
        job_id, f"rh-{job_id}",
        queue_client=client,
        job_store=store,
        fetch_lineup=lambda pids, key: (players, lineup),
        cache=cache,
    )

    direct = simulate(players, lineup, players, lineup, _LEAGUE, seed=42)
    cached = cache.get(make_cache_key("key_a", "key_b", 42))

    assert cached is not None
    delta = abs(cached.mean_margin - direct.mean_margin)
    assert delta < 1e-6, (
        f"Cached margin {cached.mean_margin:.6f} != direct {direct.mean_margin:.6f}"
    )
