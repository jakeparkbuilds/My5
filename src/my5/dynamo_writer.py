"""
DynamoDB writer for P1-cloud: converts aggregation output to DynamoDB items
and writes them via boto3's batch_writer.

Two concerns are DELIBERATELY SEPARATED:
  1. Pure conversion (lineup_row_to_item, player_row_to_item, make_lineup_key)
     — no boto3, no network, fully unit-testable.
  2. Network I/O (write_lineup_metrics, write_player_params)
     — uses boto3 batch_writer; stays behind this module's boundary.

The float → Decimal conversion rule (the real gotcha):
  boto3 rejects Python floats for DynamoDB Number attributes — it raises
  TypeError: Float types are not supported. Use Decimal types instead.
  We convert via Decimal(str(x)), NOT Decimal(x). The direct Decimal(0.1)
  produces Decimal('0.1000000000000000055511151231257827021181583404541015625')
  because the float 0.1 is already imprecise in binary. str(round(x, 4))
  gives us '0.1' and Decimal('0.1') = exactly 0.1. Our aggregation already
  rounds all floats (to 1-4 decimal places) so str() is lossless here.

NaN / None rule:
  DynamoDB rejects NaN as a Number value. Any attribute whose value is NaN
  or None is OMITTED from the item entirely. A missing attribute is
  unambiguous (no data); a NaN attribute would silently break the simulator.
  In practice our aggregation returns 0.0 for zero-denominator rates (not NaN),
  so omissions should be rare — but the guard is always-on for safety.
"""

from __future__ import annotations

import math
import os
from decimal import Decimal
from typing import Any

import boto3


# ── Endpoint resolution ───────────────────────────────────────────────────────

# Default to DynamoDB Local for development. Scripts set MY5_DYNAMO_ENDPOINT=""
# (or unset it) to use real AWS. This mirrors the `use_local` Terraform variable.
_DEFAULT_LOCAL_ENDPOINT = "http://localhost:8000"


def get_endpoint_url() -> str | None:
    """
    Read the DynamoDB endpoint from the environment.
    MY5_DYNAMO_ENDPOINT="http://localhost:8000" → DynamoDB Local (default)
    MY5_DYNAMO_ENDPOINT=""                      → real AWS (no override)
    Unset                                        → real AWS (no override)
    """
    val = os.environ.get("MY5_DYNAMO_ENDPOINT", _DEFAULT_LOCAL_ENDPOINT)
    return val if val else None


# ── Key construction ──────────────────────────────────────────────────────────


def make_lineup_key(team_id: int, athlete_ids: list[int]) -> str:
    """
    Build the canonical DynamoDB partition key for a lineup.

    Format: "{team_id}#{id_0}#{id_1}#{id_2}#{id_3}#{id_4}"

    IDs are sorted numerically so any permutation of the same 5 players
    produces an identical key. This must agree with reconstruct.py, which
    stores home_lineup / away_lineup as sorted lists — so the lineup coming
    out of aggregation is already sorted, and sorting again is idempotent.
    """
    sorted_ids = sorted(int(i) for i in athlete_ids)
    return f"{team_id}#" + "#".join(str(i) for i in sorted_ids)


# ── Type-safe attribute helpers ───────────────────────────────────────────────


def _to_decimal(x: Any) -> Decimal | None:
    """
    Convert a Python scalar to Decimal, or return None if it must be omitted.

    - None        → None  (attribute omitted)
    - float NaN   → None  (DynamoDB rejects NaN; attribute omitted)
    - float/int   → Decimal(str(x))  (str first avoids float-precision noise)
    """
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    return Decimal(str(x))


def _set(item: dict, key: str, val: Decimal | None) -> None:
    """Write key=val into item only when val is not None (never write NaN/None)."""
    if val is not None:
        item[key] = val


# ── Pure conversion — no boto3, fully testable ────────────────────────────────


def lineup_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert one lineup_metrics row (dict from polars .to_dicts()) to a
    DynamoDB item. Pure — no boto3, no I/O, safe to call in tests.

    PK: lineup_key = "{team_id}#{sorted_athlete_id_0}#...#{sorted_athlete_id_4}"
    """
    lineup: list[int] = row["lineup"]   # sorted by reconstruct.py
    team_id: int = row["team_id"]

    item: dict[str, Any] = {
        "lineup_key": make_lineup_key(team_id, lineup),
        # Store the 5 IDs as a List of Decimals so the simulator can read them
        # without re-parsing the key string.
        "lineup": [Decimal(str(aid)) for aid in lineup],
    }

    for f in (
        "team_id", "games_observed",
        "total_off_poss", "total_def_poss",
        "pts_scored", "pts_allowed",
        "opp_rim_fga", "opp_rim_fgm",
        "opp_mid_fga", "opp_mid_fgm",
        "opp_3p_fga", "opp_3p_fgm",
        "forced_to", "dreb", "dreb_opp",
        "off_rating", "def_rating", "net_rating",
        "opp_rim_fg_pct", "opp_mid_fg_pct", "opp_3p_fg_pct",
        "forced_to_rate", "dreb_rate",
    ):
        _set(item, f, _to_decimal(row.get(f)))

    return item


def player_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert one player_params row (dict from polars .to_dicts()) to a
    DynamoDB item. Pure — no boto3, no I/O, safe to call in tests.

    PK: athlete_id (Number) — ESPN's integer player ID.
    """
    item: dict[str, Any] = {
        "athlete_id": Decimal(str(row["athlete_id"])),
    }

    for f in (
        "games", "team_poss_on_floor",
        "fga", "fgm",
        "rim_a", "rim_m",
        "mid_a", "mid_m",
        "fg3a", "fg3m",
        "tov", "ft_trips",
        "fta", "ftm",
        "oreb", "oreb_opp",
        "shot_rim_rate", "shot_mid_rate", "shot_3p_rate",
        "usage_rate_raw", "rim_fg_pct_raw", "mid_fg_pct_raw",
        "fg3_pct_raw", "tov_rate_raw", "ft_rate_raw", "ft_pct_raw", "oreb_rate_raw",
        "usage_shrink_wt", "rim_pct_shrink_wt", "mid_pct_shrink_wt",
        "fg3_pct_shrink_wt", "tov_shrink_wt", "ft_rate_shrink_wt",
        "ft_pct_shrink_wt", "oreb_shrink_wt",
        "usage_rate", "rim_fg_pct", "mid_fg_pct", "fg3_pct",
        "tov_rate", "ft_rate", "ft_pct", "oreb_rate",
    ):
        _set(item, f, _to_decimal(row.get(f)))

    return item


# ── Batch I/O — uses boto3 ────────────────────────────────────────────────────


def _get_table(table_name: str, endpoint_url: str | None) -> Any:
    """
    Return a boto3 DynamoDB Table resource.
    endpoint_url=None  → real AWS (credentials from the standard chain)
    endpoint_url=str   → DynamoDB Local (dummy creds injected)
    """
    kwargs: dict[str, Any] = {"region_name": "us-east-1"}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
        kwargs["aws_access_key_id"] = "test"
        kwargs["aws_secret_access_key"] = "test"
    return boto3.resource("dynamodb", **kwargs).Table(table_name)


def write_lineup_metrics(
    rows: list[dict[str, Any]],
    endpoint_url: str | None = None,
) -> int:
    """
    Write all lineup_metrics rows to my5-lineup-metrics via batch_writer.

    batch_writer buffers items and flushes in batches of up to 25 (DynamoDB's
    BatchWriteItem limit). It handles retries for unprocessed items automatically.

    PutItem is idempotent on the PK: re-running this function overwrites existing
    items cleanly — no duplicates accumulate.

    Returns number of items written.
    """
    table = _get_table("my5-lineup-metrics", endpoint_url)
    count = 0
    with table.batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=lineup_row_to_item(row))
            count += 1
    return count


def write_player_params(
    rows: list[dict[str, Any]],
    endpoint_url: str | None = None,
) -> int:
    """
    Write all player_params rows to my5-player-params via batch_writer.
    Returns number of items written.
    """
    table = _get_table("my5-player-params", endpoint_url)
    count = 0
    with table.batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=player_row_to_item(row))
            count += 1
    return count
