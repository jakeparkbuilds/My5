"""
Tests for src/my5/dynamo_writer.py — pure conversion layer only.
No boto3, no DynamoDB, no network.

Three invariants we verify:
  1. make_lineup_key is order-independent: same 5 IDs in any order → same key.
  2. All numeric fields in a converted item are Decimal, never float.
  3. NaN / None fields are omitted (not written as NaN).
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from my5.dynamo_writer import (
    lineup_row_to_item,
    make_lineup_key,
    player_row_to_item,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

PHI_IDS = [3416, 6440, 3059318, 3133603, 4431678]
PHI_TEAM = 20
PHI_KEY_EXPECTED = "20#3416#6440#3059318#3133603#4431678"  # IDs in sorted numeric order


def _sample_lineup_row(**overrides) -> dict:
    base = {
        "lineup": list(PHI_IDS),
        "team_id": PHI_TEAM,
        "games_observed": 3,
        "total_off_poss": 87,
        "total_def_poss": 82,
        "pts_scored": 110,
        "pts_allowed": 93,
        "off_rating": 126.9,
        "def_rating": 113.0,
        "net_rating": 13.9,
        "opp_rim_fga": 30, "opp_rim_fgm": 19,
        "opp_mid_fga": 15, "opp_mid_fgm": 6,
        "opp_3p_fga": 24, "opp_3p_fgm": 8,
        "opp_rim_fg_pct": 0.623,
        "opp_mid_fg_pct": 0.400,
        "opp_3p_fg_pct": 0.333,
        "forced_to": 12, "dreb": 50, "dreb_opp": 66,
        "forced_to_rate": 0.146,
        "dreb_rate": 0.758,
    }
    base.update(overrides)
    return base


def _sample_player_row(**overrides) -> dict:
    base = {
        "athlete_id": 3059318,
        "games": 5,
        "team_poss_on_floor": 200,
        "fga": 70, "fgm": 35,
        "rim_a": 30, "rim_m": 20,
        "mid_a": 10, "mid_m": 4,
        "fg3a": 30, "fg3m": 11,
        "tov": 8,
        "ft_trips": 20,
        "fta": 24, "ftm": 20,
        "oreb": 5,
        "oreb_opp": 60,
        "shot_rim_rate": 0.429,
        "shot_mid_rate": 0.143,
        "shot_3p_rate": 0.429,
        "usage_rate_raw": 0.4900,
        "rim_fg_pct_raw": 0.6667,
        "mid_fg_pct_raw": 0.4000,
        "fg3_pct_raw": 0.3667,
        "tov_rate_raw": 0.0816,
        "ft_rate_raw": 0.1000,
        "ft_pct_raw": 0.8333,
        "oreb_rate_raw": 0.0833,
        "usage_shrink_wt": 0.800,
        "rim_pct_shrink_wt": 0.545,
        "mid_pct_shrink_wt": 0.286,
        "fg3_pct_shrink_wt": 0.545,
        "tov_shrink_wt": 0.615,
        "ft_rate_shrink_wt": 0.800,
        "ft_pct_shrink_wt": 0.490,
        "oreb_shrink_wt": 0.545,
        "usage_rate": 0.3952,
        "rim_fg_pct": 0.6334,
        "mid_fg_pct": 0.4200,
        "fg3_pct": 0.3567,
        "tov_rate": 0.0900,
        "ft_rate": 0.0950,
        "ft_pct": 0.7950,
        "oreb_rate": 0.0750,
    }
    base.update(overrides)
    return base


# ── make_lineup_key ───────────────────────────────────────────────────────────


def test_lineup_key_canonical_format():
    """Key format is '{team_id}#{sorted_id0}#...#{sorted_id4}'."""
    key = make_lineup_key(PHI_TEAM, PHI_IDS)
    assert key == PHI_KEY_EXPECTED


def test_lineup_key_order_independent():
    """Any permutation of the same 5 IDs must produce the same key."""
    key_fwd = make_lineup_key(PHI_TEAM, PHI_IDS)
    key_rev = make_lineup_key(PHI_TEAM, list(reversed(PHI_IDS)))
    key_shuf = make_lineup_key(PHI_TEAM, [PHI_IDS[2], PHI_IDS[4], PHI_IDS[0], PHI_IDS[3], PHI_IDS[1]])
    assert key_fwd == key_rev == key_shuf


def test_lineup_key_different_teams_differ():
    """Same 5 athletes on different teams must have different keys."""
    assert make_lineup_key(20, PHI_IDS) != make_lineup_key(4, PHI_IDS)


def test_lineup_key_numeric_sort_not_lexicographic():
    """
    IDs like [10, 9, 100] sort numerically to [9, 10, 100], NOT lexicographically
    to [10, 100, 9]. Lexicographic sort would produce the wrong key for IDs that
    differ in digit count.
    """
    key = make_lineup_key(1, [100, 9, 10, 20, 5])
    # Numeric sort: [5, 9, 10, 20, 100]
    assert key == "1#5#9#10#20#100"
    # Confirm it does NOT equal lexicographic sort ([10, 100, 20, 5, 9])
    assert key != "1#10#100#20#5#9"


# ── lineup_row_to_item ────────────────────────────────────────────────────────


def test_lineup_item_has_correct_key():
    item = lineup_row_to_item(_sample_lineup_row())
    assert item["lineup_key"] == PHI_KEY_EXPECTED


def test_lineup_item_key_matches_any_id_order():
    """Key inside the item must be order-independent regardless of row's lineup order."""
    row_rev = _sample_lineup_row(lineup=list(reversed(PHI_IDS)))
    item = lineup_row_to_item(row_rev)
    assert item["lineup_key"] == PHI_KEY_EXPECTED


def test_lineup_item_all_numerics_are_decimal():
    """Every numeric attribute in a lineup item must be Decimal, never float."""
    item = lineup_row_to_item(_sample_lineup_row())
    for key, val in item.items():
        if isinstance(val, list):
            for element in val:
                assert isinstance(element, Decimal), (
                    f"lineup list element is {type(element).__name__}, expected Decimal"
                )
        elif not isinstance(val, str):
            assert isinstance(val, Decimal), (
                f"Field '{key}' is {type(val).__name__}, expected Decimal"
            )


def test_lineup_item_nan_field_omitted():
    """A float NaN field must be omitted from the item, not written as NaN."""
    row = _sample_lineup_row(off_rating=float("nan"), dreb_rate=float("nan"))
    item = lineup_row_to_item(row)
    assert "off_rating" not in item, "NaN off_rating must be omitted"
    assert "dreb_rate" not in item, "NaN dreb_rate must be omitted"


def test_lineup_item_none_field_omitted():
    """A None field must be omitted (not stored as a null / empty-string value)."""
    row = _sample_lineup_row(net_rating=None)
    item = lineup_row_to_item(row)
    assert "net_rating" not in item, "None net_rating must be omitted"


def test_lineup_item_decimal_precision():
    """Decimal conversion must not introduce float-precision noise."""
    # round(0.623, 3) is a clean float in Python (no binary rep issue at 3dp)
    item = lineup_row_to_item(_sample_lineup_row(opp_rim_fg_pct=0.623))
    assert item["opp_rim_fg_pct"] == Decimal("0.623")


# ── player_row_to_item ────────────────────────────────────────────────────────


def test_player_item_pk_is_decimal():
    item = player_row_to_item(_sample_player_row())
    assert item["athlete_id"] == Decimal("3059318")
    assert isinstance(item["athlete_id"], Decimal)


def test_player_item_all_numerics_are_decimal():
    """Every numeric attribute in a player item must be Decimal, never float."""
    item = player_row_to_item(_sample_player_row())
    for key, val in item.items():
        assert not isinstance(val, float), (
            f"Field '{key}' is float, expected Decimal"
        )


def test_player_item_nan_field_omitted():
    """NaN rate fields must be omitted from the item."""
    row = _sample_player_row(usage_rate=float("nan"), oreb_rate=float("nan"))
    item = player_row_to_item(row)
    assert "usage_rate" not in item
    assert "oreb_rate" not in item


def test_player_item_none_field_omitted():
    row = _sample_player_row(ft_pct=None)
    item = player_row_to_item(row)
    assert "ft_pct" not in item


def test_player_item_zero_is_not_omitted():
    """A value of 0 (or 0.0) is valid and must NOT be omitted."""
    row = _sample_player_row(fg3a=0, fg3_pct_raw=0.0)
    item = player_row_to_item(row)
    assert "fg3a" in item
    assert item["fg3a"] == Decimal("0")
    assert "fg3_pct_raw" in item
    assert item["fg3_pct_raw"] == Decimal("0.0")
