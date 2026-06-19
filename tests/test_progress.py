"""
Tests for the P2-B progress feed: engine callback + worker DynamoDB writes.

Four tests:
  1. on_progress=None → identical SimResult to a run without the callback
     (seal intact: same seed, same result).
  2. A callback-attached run fires at most every _PROGRESS_INTERVAL sims, never
     every sim. All sims_done values are multiples of _PROGRESS_INTERVAL.
  3. Same seed with and without callback → byte-identical SimResult fields
     (determinism proof: callback does not touch the RNG).
  4. handle_job wires the callback → progress_sims is written to the job record.
"""
from __future__ import annotations

import dataclasses
import time
import uuid
from decimal import Decimal
from typing import Any

from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages, SimResult, _PROGRESS_INTERVAL, simulate

# ── Shared fixtures (duplicated from test_queue for independence) ─────────────

_LEAGUE = LeagueAverages(
    usage_rate=0.19, rim_fg_pct=0.616, mid_fg_pct=0.410, fg3_pct=0.379,
    tov_rate=0.1119, ft_rate=0.038, ft_pct=0.770, oreb_rate=0.065,
    shot_rim_rate=0.450, shot_mid_rate=0.175, shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616, opp_mid_fg_pct=0.410, opp_3p_fg_pct=0.379,
    forced_to_rate=0.126, dreb_rate=0.730,
)


def _make_player(**overrides) -> dict:
    base = dict(
        usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
        shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
        rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
    )
    base.update(overrides)
    return base


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


# ── In-memory fakes (same pattern as test_queue.py) ──────────────────────────


class FakeTable:
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
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deleted: list[str] = []

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> dict:
        msg_id = str(uuid.uuid4())
        self.messages.append({
            "MessageId": msg_id, "Body": MessageBody,
            "ReceiptHandle": f"rh-{msg_id}",
        })
        return {"MessageId": msg_id}

    def receive_message(self, *, QueueUrl: str, **kwargs) -> dict:
        if not self.messages:
            return {}
        msg = self.messages[0]
        return {"Messages": [{
            "Body": msg["Body"], "ReceiptHandle": msg["ReceiptHandle"],
            "Attributes": {"ApproximateReceiveCount": "1"},
        }]}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> dict:
        self.deleted.append(ReceiptHandle)
        self.messages = [m for m in self.messages if m["ReceiptHandle"] != ReceiptHandle]
        return {}


def _make_store_and_client():
    table = FakeTable()
    sqs = FakeSQS()
    store = JobStore(table=table)
    client = QueueClient(sqs_client=sqs, queue_url="fake://main")
    return store, client, table, sqs


def _make_job_record(job_id: str, seed: int = 42) -> dict:
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


# ── Test 1: on_progress=None → same result as calling without the parameter ──


def test_no_callback_same_result_as_baseline():
    """simulate(on_progress=None) must return the same SimResult as the original signature."""
    players = _five_players()
    lineup = _sample_lineup()

    baseline = simulate(players, lineup, players, lineup, _LEAGUE, seed=7)
    with_none = simulate(players, lineup, players, lineup, _LEAGUE, seed=7, on_progress=None)

    assert baseline.mean_margin == with_none.mean_margin
    assert baseline.n_sims == with_none.n_sims
    assert baseline.ci_half_width == with_none.ci_half_width
    assert baseline.converged == with_none.converged


# ── Test 2: callback fires every _PROGRESS_INTERVAL sims, not every sim ──────


def test_callback_fires_at_interval_not_every_sim():
    """
    on_progress must be called at most ceil(n_sims / _PROGRESS_INTERVAL) times,
    and every call's sims_done must be an exact multiple of _PROGRESS_INTERVAL.
    """
    players = _five_players()
    lineup = _sample_lineup()

    calls: list[tuple[int, float]] = []
    result = simulate(players, lineup, players, lineup, _LEAGUE, seed=13,
                      on_progress=lambda s, ci: calls.append((s, ci)))

    # Must not be called every sim (that would equal n_sims calls).
    assert len(calls) < result.n_sims, (
        f"Callback called {len(calls)} times for {result.n_sims} sims — "
        f"looks like it fires every sim instead of every {_PROGRESS_INTERVAL}"
    )

    # Every call's sims_done must be a multiple of _PROGRESS_INTERVAL.
    for sims_done, _ in calls:
        assert sims_done % _PROGRESS_INTERVAL == 0, (
            f"sims_done={sims_done} is not a multiple of _PROGRESS_INTERVAL={_PROGRESS_INTERVAL}"
        )

    # Approximate upper bound: one call per interval (allow one extra for rounding).
    max_expected = (result.n_sims // _PROGRESS_INTERVAL) + 1
    assert len(calls) <= max_expected, (
        f"Too many calls: {len(calls)} > {max_expected}"
    )

    # CI values must be positive floats.
    for _, ci in calls:
        assert ci > 0.0, f"CI should be positive, got {ci}"


# ── Test 3: callback does not affect determinism ──────────────────────────────


def test_same_seed_with_and_without_callback_identical_result():
    """
    Attaching an on_progress callback must not change the SimResult.
    The callback is a pure side-effect; the RNG sequence is unaffected.
    """
    players = _five_players()
    lineup = _sample_lineup()

    calls: list[tuple[int, float]] = []
    with_callback = simulate(players, lineup, players, lineup, _LEAGUE, seed=99,
                             on_progress=lambda s, ci: calls.append((s, ci)))
    without_callback = simulate(players, lineup, players, lineup, _LEAGUE, seed=99)

    assert with_callback.mean_margin == without_callback.mean_margin, (
        f"mean_margin differs: {with_callback.mean_margin} vs {without_callback.mean_margin}"
    )
    assert with_callback.n_sims == without_callback.n_sims
    assert with_callback.ci_half_width == without_callback.ci_half_width
    assert with_callback.converged == without_callback.converged

    # Callback must have fired at least once (n_sims >= _MIN_SIMS=100 >= _PROGRESS_INTERVAL=50).
    assert len(calls) >= 1, "Expected at least one progress callback call"


# ── Test 4: handle_job writes progress_sims to the job record ─────────────────


def test_handle_job_writes_progress_to_dynamo():
    """
    handle_job must write progress_sims (and progress_ci) to the DynamoDB job
    record while the engine runs. After handle_job returns, progress_sims > 0.
    """
    store, client, table, sqs = _make_store_and_client()
    job_id = str(uuid.uuid4())
    players = _five_players()
    lineup = _sample_lineup()

    record = _make_job_record(job_id, seed=55)
    table.put_item(Item=record)

    rh = f"rh-{job_id}"
    sqs.messages.append({
        "MessageId": "m1",
        "Body": f'{{"job_id": "{job_id}"}}',
        "ReceiptHandle": rh,
    })

    def _fetch(player_ids, lineup_key):
        return players, lineup

    status = handle_job(job_id, rh,
                        queue_client=client,
                        job_store=store,
                        fetch_lineup=_fetch)

    assert status == "done"

    job = store.get_job(job_id)
    assert job["status"] == "done"

    # progress_sims must have been written at least once during the run.
    # The first write happens at n=_PROGRESS_INTERVAL (50); n_sims >= _MIN_SIMS=100.
    progress_sims = job.get("progress_sims")
    assert progress_sims is not None, (
        "progress_sims missing from job record — handle_job did not wire on_progress"
    )
    assert int(progress_sims) >= _PROGRESS_INTERVAL, (
        f"progress_sims={progress_sims} should be >= {_PROGRESS_INTERVAL}"
    )

    # progress_ci must also be present and positive.
    progress_ci = job.get("progress_ci")
    assert progress_ci is not None, "progress_ci missing from job record"
    assert float(progress_ci) > 0.0, f"progress_ci={progress_ci} should be positive"
