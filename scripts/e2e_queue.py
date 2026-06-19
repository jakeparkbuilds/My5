"""
End-to-end proof: submit_job → queue → handle_job → DONE.

Requires both local containers running (MY5_ENV=local default):
  docker run --rm -d -p 8000:8000 --name dynamodb-local amazon/dynamodb-local
  docker run --rm -d -p 9324:9324 -p 9325:9325 --name elasticmq softwaremill/elasticmq-native:latest

And Terraform applied (which creates tables + queues):
  terraform -chdir=infra init && terraform -chdir=infra apply -auto-approve

And data loaded into DynamoDB Local:
  python scripts/load_dynamo.py

Run this script:
  .venv/bin/python3 scripts/e2e_queue.py

What it proves:
  Flow A — valid matchup:
    submit_job(real lineup A vs real lineup B, seed=42)
      → QUEUED record in DynamoDB + message in ElasticMQ
      → handle_job() runs engine
      → DONE record with result matching direct simulate() call (same seed)

  Flow B — invalid lineup:
    submit_job(bogus lineup key, seed=99)
      → QUEUED record in DynamoDB
      → handle_job() raises LineupNotFoundError
      → FAILED record with error_type=invalid_lineup
      → message deleted (no retry)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json
from decimal import Decimal

import boto3

from my5.config import DLQ_URL, SQS_QUEUE_URL, make_dynamo_resource, make_sqs_client
from my5.job_store import JobStore
from my5.job_worker import _default_fetch_lineup, handle_job
from my5.queue_client import QueueClient
from my5.simulator import simulate
from my5.submit_job import _DEFAULT_LEAGUE, submit_job


def _hr(label: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print("─" * 60)


def _pick_two_lineups() -> tuple[str, list[int], str, list[int]]:
    """
    Scan my5-lineup-metrics for two lineups with games_observed >= 3
    that belong to different teams. Returns (key_a, ids_a, key_b, ids_b).
    """
    dynamo = make_dynamo_resource()
    table = dynamo.Table("my5-lineup-metrics")

    found = []
    seen_teams: set[int] = set()
    last_key = None

    while len(found) < 2:
        kwargs: dict = {
            "FilterExpression": "games_observed >= :min_g",
            "ExpressionAttributeValues": {":min_g": Decimal("3")},
            "ProjectionExpression": "lineup_key, #t, lineup, games_observed",
            "ExpressionAttributeNames": {"#t": "team_id"},
            "Limit": 50,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            team_id = int(item["team_id"])
            if team_id in seen_teams:
                continue
            player_ids = [int(x) for x in item["lineup"]]
            found.append((item["lineup_key"], player_ids, int(item["games_observed"])))
            seen_teams.add(team_id)
            if len(found) >= 2:
                break

        last_key = resp.get("LastEvaluatedKey")
        if not last_key and len(found) < 2:
            raise RuntimeError("Could not find 2 lineups with games_observed >= 3. "
                               "Run load_dynamo.py first.")

    a_key, a_ids, a_games = found[0]
    b_key, b_ids, b_games = found[1]
    print(f"  Team A lineup: {a_key}  ({a_games} games)")
    print(f"  Team B lineup: {b_key}  ({b_games} games)")
    return a_key, a_ids, b_key, b_ids


def _check_containers() -> None:
    """Verify DynamoDB Local and ElasticMQ are reachable."""
    import urllib.request
    errors = []
    for name, url in [("DynamoDB Local", "http://localhost:8000/"),
                      ("ElasticMQ", "http://localhost:9324/")]:
        try:
            urllib.request.urlopen(url, timeout=2)
        except Exception:
            pass  # ElasticMQ may return a non-200 on bare GET — that's fine
        # Real check: boto3 call
    try:
        sqs = make_sqs_client()
        sqs.list_queues()
    except Exception as exc:
        errors.append(f"ElasticMQ not reachable: {exc}")
    try:
        dynamo = make_dynamo_resource()
        dynamo.meta.client.list_tables()
    except Exception as exc:
        errors.append(f"DynamoDB Local not reachable: {exc}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        sys.exit(1)
    print("  ✓ DynamoDB Local (port 8000) — OK")
    print("  ✓ ElasticMQ      (port 9324) — OK")


def flow_a_valid_matchup(
    a_key: str, a_ids: list[int],
    b_key: str, b_ids: list[int],
) -> None:
    _hr("FLOW A — valid matchup (real lineups, seed=42)")

    store = JobStore()
    client = QueueClient()
    dynamo = make_dynamo_resource()

    # 1. Submit
    job_id = submit_job(
        team_a_key=a_key, team_a_player_ids=a_ids,
        team_b_key=b_key, team_b_player_ids=b_ids,
        seed=42,
        league=_DEFAULT_LEAGUE,
        job_store=store,
        queue_client=client,
    )
    print(f"\n  Submitted job_id = {job_id}")

    # 2. Verify QUEUED in DynamoDB
    job = store.get_job(job_id)
    assert job["status"] == "queued", f"Expected queued, got {job['status']!r}"
    print(f"  DynamoDB status  = {job['status']} ✓")

    # 3. Verify message in ElasticMQ
    sqs = make_sqs_client()
    msgs = sqs.receive_message(
        QueueUrl=SQS_QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=2,
    ).get("Messages", [])
    assert msgs, "No message found in ElasticMQ after submit"
    body = json.loads(msgs[0]["Body"])
    assert body["job_id"] == job_id
    print(f"  ElasticMQ msg    = {{job_id: {job_id[:8]}...}} ✓")

    # Put the message back (we just peeked; handle_job will receive it for real)
    sqs.change_message_visibility(
        QueueUrl=SQS_QUEUE_URL,
        ReceiptHandle=msgs[0]["ReceiptHandle"],
        VisibilityTimeout=0,  # make visible immediately
    )

    # 4. Receive + handle
    msgs2 = client.receive(wait_seconds=2, visibility_timeout=60)
    assert msgs2, "handle_job: no message in queue"
    msg = msgs2[0]
    assert msg["job_id"] == job_id

    def fetch_lineup(player_ids, lineup_key):
        return _default_fetch_lineup(player_ids, lineup_key, dynamo_resource=dynamo)

    status = handle_job(
        job_id, msg["receipt_handle"],
        queue_client=client,
        job_store=store,
        fetch_lineup=fetch_lineup,
    )
    print(f"  handle_job status = {status}")
    assert status == "done", f"Expected done, got {status!r}"

    # 5. Verify DONE in DynamoDB
    job = store.get_job(job_id)
    assert job["status"] == "done"
    result = job["result"]
    print(f"  DynamoDB status   = {job['status']} ✓")
    print(f"  mean_margin       = {result['mean_margin']:.3f} pts")
    print(f"  ci_half_width     = {result['ci_half_width']:.3f} pts")
    print(f"  n_sims            = {int(result['n_sims'])}")
    print(f"  equiv_net_rating  = {result['equiv_net_rating']:.2f} pts/100")
    print(f"  converged         = {result['converged']}")

    # 6. Compare with direct engine call (same seed must produce same result)
    def fetch_direct(player_ids, lineup_key):
        return _default_fetch_lineup(player_ids, lineup_key, dynamo_resource=dynamo)

    a_players, a_lineup = fetch_direct(a_ids, a_key)
    b_players, b_lineup = fetch_direct(b_ids, b_key)
    direct = simulate(a_players, a_lineup, b_players, b_lineup, _DEFAULT_LEAGUE, seed=42)
    delta = abs(result["mean_margin"] - direct.mean_margin)
    assert delta < 1e-6, (
        f"Stored margin {result['mean_margin']:.6f} ≠ direct call {direct.mean_margin:.6f}"
    )
    print(f"  Direct call margin = {direct.mean_margin:.3f} pts  (delta={delta:.2e}) ✓")
    print("\n  FLOW A PASSED ✓")


def flow_b_invalid_lineup() -> None:
    _hr("FLOW B — invalid lineup key")

    store = JobStore()
    client = QueueClient()

    job_id = submit_job(
        team_a_key="BOGUS#KEY#THAT#DOES#NOT#EXIST",
        team_a_player_ids=[99999, 99998, 99997, 99996, 99995],
        team_b_key="hypothetical",
        team_b_player_ids=[88888, 88887, 88886, 88885, 88884],
        seed=99,
        league=_DEFAULT_LEAGUE,
        job_store=store,
        queue_client=client,
    )
    print(f"\n  Submitted job_id = {job_id}")

    job = store.get_job(job_id)
    assert job["status"] == "queued"
    print(f"  DynamoDB status  = {job['status']} ✓")

    msgs = client.receive(wait_seconds=2, visibility_timeout=60)
    assert msgs and msgs[0]["job_id"] == job_id
    msg = msgs[0]

    status = handle_job(
        job_id, msg["receipt_handle"],
        queue_client=client,
        job_store=store,
    )
    print(f"  handle_job status = {status}")
    assert status == "failed"

    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error_type"] == "invalid_lineup"
    print(f"  DynamoDB status   = {job['status']} ✓")
    print(f"  error_type        = {job['error_type']} ✓")
    print(f"  error_message     = {job['error_message'][:80]}...")

    # Verify message was deleted (no messages left in queue after fail-fast)
    remaining = client.receive(wait_seconds=1)
    assert not remaining, "Message was NOT deleted after invalid_lineup — expected delete"
    print("  SQS message deleted after fail-fast ✓")
    print("\n  FLOW B PASSED ✓")


def main() -> None:
    print("=" * 60)
    print("  My5 P2-A End-to-End Queue Proof")
    print("  MY5_ENV=local  (DynamoDB Local + ElasticMQ)")
    print("=" * 60)

    _hr("Container health check")
    _check_containers()

    _hr("Selecting lineups from DynamoDB Local")
    a_key, a_ids, b_key, b_ids = _pick_two_lineups()

    flow_a_valid_matchup(a_key, a_ids, b_key, b_ids)
    flow_b_invalid_lineup()

    print("\n" + "=" * 60)
    print("  ALL FLOWS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
