"""
Load aggregation output into DynamoDB and verify the round-trip.

Usage (DynamoDB Local default):
    source .venv/bin/activate
    python scripts/load_dynamo.py

For real AWS (future), set:
    MY5_DYNAMO_ENDPOINT="" python scripts/load_dynamo.py

Runs aggregation over the full 52-game slice, writes both tables,
then reads back two known items to confirm Decimal conversion is lossless.
"""

from __future__ import annotations

import os
import boto3
from decimal import Decimal

from my5.aggregate import run_aggregation
from my5.dynamo_writer import (
    get_endpoint_url,
    make_lineup_key,
    write_lineup_metrics,
    write_player_params,
)

GAME_IDS = [
    401585087, 401585088, 401585089, 401585090, 401585091, 401585092,
    401585093, 401585094, 401585095, 401585097, 401585098, 401585099,
    401585096, 401585100, 401585101, 401585102, 401585103, 401585104,
    401585107, 401585108, 401585109, 401585110, 401585111, 401585112,
    401585113, 401585114, 401585115, 401585116, 401585117, 401585118,
    401585119, 401585120, 401585121, 401585122, 401585123, 401585124,
    401585134, 401585135, 401585136, 401585137, 401585138, 401585139,
    401585145, 401585146, 401585147, 401585148, 401585149, 401585150,
    401585151, 401585153, 401585152, 401585154,
]

# Known validation anchors from Phase B validation run.
# Any deviation after DynamoDB round-trip = Decimal conversion corrupted the value.
PHI_STARTERS = [3416, 6440, 3059318, 3133603, 4431678]   # PHI team_id=20
PHI_TEAM_ID = 20
EMBIID_ID = 3059318


def _get_table(name: str, endpoint_url: str | None):
    kwargs = {"region_name": "us-east-1"}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
        kwargs["aws_access_key_id"] = "test"
        kwargs["aws_secret_access_key"] = "test"
    return boto3.resource("dynamodb", **kwargs).Table(name)


def main() -> None:
    endpoint = get_endpoint_url()
    print(f"\nDynamoDB endpoint: {endpoint or 'real AWS'}")

    # ── 1. Run aggregation ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 1: Running aggregation (52 games) ...")
    print("=" * 60)
    lineup_metrics, player_params, corrupted_stints = run_aggregation(GAME_IDS)
    print(f"  Lineup rows: {len(lineup_metrics)}")
    print(f"  Player rows: {len(player_params)}")
    if corrupted_stints:
        print(f"  !! {len(corrupted_stints)} corrupted stints — investigate!")
    else:
        print("  Corrupted stints: 0 (OK)")

    # Pull source values BEFORE writing, so we can compare after reading back.
    lineup_rows = lineup_metrics.to_dicts()
    player_rows = player_params.to_dicts()

    phi_key = make_lineup_key(PHI_TEAM_ID, PHI_STARTERS)
    phi_source = next(
        (r for r in lineup_rows if make_lineup_key(r["team_id"], r["lineup"]) == phi_key),
        None,
    )
    embiid_source = next(
        (r for r in player_rows if r["athlete_id"] == EMBIID_ID),
        None,
    )

    if phi_source is None:
        print(f"\n  !! PHI starters lineup not found in aggregation output.")
        print(f"     key={phi_key}")
        return
    if embiid_source is None:
        print(f"\n  !! Embiid (athlete_id={EMBIID_ID}) not found in player_params.")
        return

    print(f"\n  Source PHI lineup: off={phi_source['off_rating']}  "
          f"def={phi_source['def_rating']}  net={phi_source['net_rating']}")
    print(f"  Source Embiid:     usage_rate={embiid_source['usage_rate']}")

    # ── 2. Write to DynamoDB ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 2: Writing to DynamoDB ...")
    print("=" * 60)
    n_lineups = write_lineup_metrics(lineup_rows, endpoint_url=endpoint)
    print(f"  Lineup items written: {n_lineups}")
    n_players = write_player_params(player_rows, endpoint_url=endpoint)
    print(f"  Player items written: {n_players}")

    # ── 3. Read-back verification ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 3: Round-trip read-back ...")
    print("=" * 60)

    lineup_table = _get_table("my5-lineup-metrics", endpoint)
    player_table = _get_table("my5-player-params", endpoint)

    # GetItem for PHI starters lineup
    phi_resp = lineup_table.get_item(Key={"lineup_key": phi_key})
    phi_item = phi_resp.get("Item")
    if phi_item is None:
        print(f"  !! FAIL: PHI starters item not found in DynamoDB (key={phi_key})")
        return

    # GetItem for Embiid
    embiid_resp = player_table.get_item(Key={"athlete_id": Decimal(str(EMBIID_ID))})
    embiid_item = embiid_resp.get("Item")
    if embiid_item is None:
        print(f"  !! FAIL: Embiid item not found in DynamoDB (athlete_id={EMBIID_ID})")
        return

    # ── Comparison ────────────────────────────────────────────────────────────
    print("\n  PHI starters lineup read-back:")
    phi_off_db  = float(phi_item["off_rating"])
    phi_def_db  = float(phi_item["def_rating"])
    phi_net_db  = float(phi_item["net_rating"])
    phi_off_src = phi_source["off_rating"]
    phi_def_src = phi_source["def_rating"]
    phi_net_src = phi_source["net_rating"]

    print(f"    off_rating:  source={phi_off_src}  db={phi_off_db}  "
          + ("OK" if phi_off_db == phi_off_src else f"!! MISMATCH"))
    print(f"    def_rating:  source={phi_def_src}  db={phi_def_db}  "
          + ("OK" if phi_def_db == phi_def_src else f"!! MISMATCH"))
    print(f"    net_rating:  source={phi_net_src}  db={phi_net_db}  "
          + ("OK" if phi_net_db == phi_net_src else f"!! MISMATCH"))

    # Cross-check against the Phase B validation anchors
    print(f"\n    Phase B anchors (from validation run):  off=126.9  def=113.0  net=+13.9")
    if phi_off_src == 126.9 and phi_def_src == 113.0 and phi_net_src == 13.9:
        print("    Anchors MATCH — aggregation is stable across runs.")
    else:
        print(f"    Anchors DIFFER from Phase B: off={phi_off_src}  def={phi_def_src}  net={phi_net_src}")
        print("    (This means aggregation output changed — check for code changes.)")

    print("\n  Embiid player params read-back:")
    embiid_usage_db  = float(embiid_item["usage_rate"])
    embiid_usage_src = embiid_source["usage_rate"]
    print(f"    usage_rate:  source={embiid_usage_src}  db={embiid_usage_db}  "
          + ("OK" if embiid_usage_db == embiid_usage_src else "!! MISMATCH"))
    print(f"    Phase B anchor: ~0.395")

    # Overall verdict
    all_match = (
        phi_off_db == phi_off_src
        and phi_def_db == phi_def_src
        and phi_net_db == phi_net_src
        and embiid_usage_db == embiid_usage_src
    )

    print("\n" + "=" * 60)
    if all_match:
        print("ROUND-TRIP RESULT: PASS — Decimal conversion is lossless.")
        print(f"  {n_lineups} lineup items + {n_players} player items written and verified.")
    else:
        print("ROUND-TRIP RESULT: FAIL — at least one value mismatched.")
        print("Check the output above for !! MISMATCH lines.")
    print("=" * 60)


if __name__ == "__main__":
    main()
