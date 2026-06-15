"""
Unit tests for src/my5/aggregate.py.

All tests use hand-constructed event sequences so no network calls are needed.
The possession counts in each test are verified manually before being asserted.
"""

from __future__ import annotations

import polars as pl
import pytest

from my5.aggregate import (
    _is_final_ft,
    _is_ft_trip_start,
    _shot_zone,
    _tag_and_ones,
    run_aggregation,
)
from my5.reconstruct import reconstruct_lineups

# ── Shared fixtures ────────────────────────────────────────────────────────────

HOME_ID = 20   # PHI
AWAY_ID = 4    # CHI

HOME_STARTERS = [3416, 6440, 3059318, 3133603, 4431678]
AWAY_STARTERS = [4431687, 6585, 3978, 2991350, 4395651]
BENCH = 4397002  # CHI bench player


def _roster_df() -> pl.DataFrame:
    rows = []
    for aid in HOME_STARTERS:
        rows.append({"athlete_id": aid, "team_id": HOME_ID, "starter": True, "active": True, "did_not_play": False})
    for aid in AWAY_STARTERS:
        rows.append({"athlete_id": aid, "team_id": AWAY_ID, "starter": True, "active": True, "did_not_play": False})
    rows.append({"athlete_id": BENCH, "team_id": AWAY_ID, "starter": False, "active": True, "did_not_play": False})
    return pl.DataFrame(rows)


def _row(
    gpn: int,
    type_text: str,
    team_id: int,
    *,
    p0: int | None = None,
    scoring: bool = False,
    score_val: int | None = None,
    shooting: bool = False,
    pts_att: int | None = None,
    clock: str = "10:00",
) -> dict:
    return {
        "game_play_number": gpn,
        "sequenceNumber": gpn,
        "type.text": type_text,
        "team.id": str(team_id),
        "homeTeamId": HOME_ID,
        "awayTeamId": AWAY_ID,
        "participants.0.athlete.id": str(p0) if p0 is not None else None,
        "participants.1.athlete.id": None,
        "period.number": 1,
        "scoringPlay": scoring,
        "scoreValue": score_val,
        "shootingPlay": shooting,
        "pointsAttempted": pts_att,
        "clock.displayValue": clock,
        "coordinate.x": None,
        "coordinate.y": None,
    }


# ── Helpers: unit tests ────────────────────────────────────────────────────────


def test_shot_zone_three():
    assert _shot_zone(3, "3PT Jump Shot") == "three"


def test_shot_zone_rim_layup():
    assert _shot_zone(2, "Driving Layup Shot") == "rim"


def test_shot_zone_rim_dunk():
    assert _shot_zone(2, "Dunk") == "rim"


def test_shot_zone_rim_tip():
    assert _shot_zone(2, "Tip Shot") == "rim"


def test_shot_zone_rim_finger_roll():
    assert _shot_zone(2, "Finger Roll Layup") == "rim"


def test_shot_zone_mid():
    assert _shot_zone(2, "Jump Shot") == "mid"
    assert _shot_zone(2, "Pullup Jump Shot") == "mid"
    assert _shot_zone(2, "Floating Jump Shot") == "mid"


def test_is_final_ft():
    assert _is_final_ft("Free Throw - 2 of 2")
    assert _is_final_ft("Free Throw - 3 of 3")
    assert _is_final_ft("Free Throw - 1 of 1")
    assert _is_final_ft("Free Throw - Flagrant 2 of 2")
    assert not _is_final_ft("Free Throw - 1 of 2")
    assert not _is_final_ft("Free Throw - 2 of 3")


def test_is_ft_trip_start():
    assert _is_ft_trip_start("Free Throw - 1 of 2")
    assert _is_ft_trip_start("Free Throw - 1 of 3")
    assert _is_ft_trip_start("Free Throw - 1 of 1")
    assert _is_ft_trip_start("Free Throw - Clear Path 1 of 2")
    assert not _is_ft_trip_start("Free Throw - 2 of 2")
    assert not _is_ft_trip_start("Free Throw - 3 of 3")


def test_tag_and_ones_basic():
    """Made FG at row 0, matching FT-1-of-1 at row 2 → both flagged."""
    rows = [
        {"scoringPlay": True, "scoreValue": 2, "type.text": "Layup Shot",
         "participants.0.athlete.id": "100", "team.id": "20", "clock.displayValue": "5:00"},
        {"scoringPlay": False, "scoreValue": None, "type.text": "Shooting Foul",
         "participants.0.athlete.id": "200", "team.id": "4", "clock.displayValue": "5:00"},
        {"scoringPlay": False, "scoreValue": None, "type.text": "Free Throw - 1 of 1",
         "participants.0.athlete.id": "100", "team.id": "20", "clock.displayValue": "5:00"},
    ]
    fg_idxs, ft_idxs = _tag_and_ones(rows)
    assert 0 in fg_idxs
    assert 2 in ft_idxs


def test_tag_and_ones_no_match_different_player():
    """FT-1-of-1 by a different player → not an and-one."""
    rows = [
        {"scoringPlay": True, "scoreValue": 2, "type.text": "Layup Shot",
         "participants.0.athlete.id": "100", "team.id": "20", "clock.displayValue": "5:00"},
        {"scoringPlay": False, "scoreValue": None, "type.text": "Free Throw - 1 of 1",
         "participants.0.athlete.id": "999", "team.id": "20", "clock.displayValue": "5:00"},
    ]
    fg_idxs, ft_idxs = _tag_and_ones(rows)
    assert not fg_idxs
    assert not ft_idxs


# ── Possession counting ────────────────────────────────────────────────────────


def _run_aggregation_on_rows(event_rows: list[dict]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Run aggregation on a synthetic game routed through a fake loader.
    We reconstruct directly to avoid needing cached parquet files.
    """
    plays = pl.DataFrame(event_rows)
    roster = _roster_df()
    recon = reconstruct_lineups(plays, roster)
    recon_rows = recon.to_dicts()

    from my5.aggregate import (
        _tag_and_ones, _accumulate_game, _compute_league_avgs,
        _build_lineup_metrics, _build_player_params,
    )

    lineup_acc: dict = {}
    player_acc: dict = {}
    corrupted_stints: list = []
    fg_idxs, ft_idxs = _tag_and_ones(recon_rows)
    _accumulate_game(999, recon_rows, fg_idxs, ft_idxs, lineup_acc, player_acc, corrupted_stints)
    league_avgs = _compute_league_avgs(player_acc)
    lm = _build_lineup_metrics(lineup_acc)
    pp = _build_player_params(player_acc, league_avgs)
    return lm, pp


def test_made_fg_counts_one_possession():
    """
    Single made FG by home team → home off_poss=1, away def_poss=1.
    Manual walk: row 0 made 2PT → home possession end.
    """
    rows = [
        _row(1, "Layup Shot", HOME_ID, p0=HOME_STARTERS[0], scoring=True, score_val=2, shooting=True, pts_att=2),
    ]
    lm, _ = _run_aggregation_on_rows(rows)
    home_row = lm.filter(pl.col("team_id") == HOME_ID).row(0, named=True)
    away_row = lm.filter(pl.col("team_id") == AWAY_ID).row(0, named=True)
    assert home_row["total_off_poss"] == 1
    assert away_row["total_def_poss"] == 1


def test_and_one_counts_as_one_possession():
    """
    Made FG + FT-1-of-1 by same player at same clock → ONE possession, not two.
    Manual walk:
      row 0: made 2PT (is_and_one_fg=True) → NOT a possession end
      row 1: Free Throw - 1 of 1 made (is_and_one_ft=True) → possession end for home
    Result: home off_poss=1, NOT 2.
    """
    rows = [
        _row(1, "Layup Shot", HOME_ID, p0=HOME_STARTERS[0],
             scoring=True, score_val=2, shooting=True, pts_att=2, clock="8:00"),
        _row(2, "Free Throw - 1 of 1", HOME_ID, p0=HOME_STARTERS[0],
             scoring=True, score_val=1, clock="8:00"),
    ]
    lm, _ = _run_aggregation_on_rows(rows)
    home_row = lm.filter(pl.col("team_id") == HOME_ID).row(0, named=True)
    assert home_row["total_off_poss"] == 1, (
        f"And-one should count as 1 possession, got {home_row['total_off_poss']}"
    )


def test_non_and_one_lone_ft_counts_as_usage_trip():
    """
    A Free Throw - 1 of 1 that is NOT an and-one (no preceding made FG by same
    player) must count as an FT trip in the usage numerator.

    Setup: one row only — a standalone FT-1-of-1 (flagrant / clear-path scenario).
    Expected: ft_trips=1 for that player.
    """
    rows = [
        _row(1, "Free Throw - 1 of 1", AWAY_ID, p0=AWAY_STARTERS[0],
             scoring=True, score_val=1, clock="5:30"),
    ]
    _, pp = _run_aggregation_on_rows(rows)
    player_row = pp.filter(pl.col("athlete_id") == AWAY_STARTERS[0])
    assert len(player_row) == 1
    ft_trips = player_row.row(0, named=True)["ft_trips"]
    assert ft_trips == 1, f"Standalone FT-1-of-1 must be 1 FT trip, got {ft_trips}"


def test_defensive_rebound_ends_opponent_possession():
    """
    Home team misses a shot; away team gets the DREB.
    Manual walk:
      row 0: home missed 2PT → NOT a possession end by itself
      row 1: away Defensive Rebound → home off_poss=1 (opponent's possession just ended)
    """
    rows = [
        _row(1, "Jump Shot", HOME_ID, p0=HOME_STARTERS[0],
             scoring=False, shooting=True, pts_att=2),
        _row(2, "Defensive Rebound", AWAY_ID, p0=AWAY_STARTERS[0]),
    ]
    lm, _ = _run_aggregation_on_rows(rows)
    home_row = lm.filter(pl.col("team_id") == HOME_ID).row(0, named=True)
    assert home_row["total_off_poss"] == 1


def test_inv1_stint_fully_excluded():
    """
    A stint containing an inv1_count violation must contribute zero possessions.

    Setup: ghost player_out sub (inv1_count violation) then a made FG in the same
    stint. The FG should NOT be counted since the entire stint is excluded.
    """
    ghost_out = 9999999
    # Add ghost to roster so it passes the player_team map
    rows_plays = [
        {
            "game_play_number": 1, "sequenceNumber": 1,
            "type.text": "Substitution",
            "team.id": str(AWAY_ID),
            "homeTeamId": HOME_ID, "awayTeamId": AWAY_ID,
            "participants.0.athlete.id": str(BENCH),
            "participants.1.athlete.id": str(ghost_out),
            "period.number": 1,
            "scoringPlay": False, "scoreValue": None,
            "shootingPlay": False, "pointsAttempted": None,
            "clock.displayValue": "10:00",
        },
        {
            "game_play_number": 2, "sequenceNumber": 2,
            "type.text": "Layup Shot",
            "team.id": str(HOME_ID),
            "homeTeamId": HOME_ID, "awayTeamId": AWAY_ID,
            "participants.0.athlete.id": str(HOME_STARTERS[0]),
            "participants.1.athlete.id": None,
            "period.number": 1,
            "scoringPlay": True, "scoreValue": 2,
            "shootingPlay": True, "pointsAttempted": 2,
            "clock.displayValue": "9:50",
        },
    ]
    plays = pl.DataFrame(rows_plays)
    roster = _roster_df()
    recon = reconstruct_lineups(plays, roster)
    recon_rows = recon.to_dicts()

    from my5.aggregate import (
        _tag_and_ones, _accumulate_game, _compute_league_avgs,
        _build_lineup_metrics, _build_player_params,
    )
    lineup_acc: dict = {}
    player_acc: dict = {}
    fg_idxs, ft_idxs = _tag_and_ones(recon_rows)
    _accumulate_game(999, recon_rows, fg_idxs, ft_idxs, lineup_acc, player_acc, [])
    league_avgs = _compute_league_avgs(player_acc)
    lm = _build_lineup_metrics(lineup_acc)

    # The inv1 sub fires on the pre-sub stint (which is excluded).
    # The subsequent FG is in a new stint whose lineup has 6 players (corrupted by
    # the ghost add). Aggregation also skips stints with non-5 player counts.
    # Either way, total possessions must be 0.
    total_poss = lm["total_off_poss"].sum() if len(lm) > 0 else 0
    assert total_poss == 0, (
        f"inv1_count corruption must be fully excluded; got {total_poss} possessions"
    )


def test_inv2_row_dropped_stint_survives():
    """
    A row with inv2_participant is dropped; the rest of the stint is kept.

    Setup:
      row 1: Inv2 violation (ghost participant) — should be dropped
      row 2: Clean made FG in the SAME stint — should be counted

    The ghost participant is on team AWAY so its "Jump Shot" gets flagged inv2.
    The home team's FG in the same lineup should still produce a possession.
    """
    ghost = 7777777

    rows_plays = [
        {
            "game_play_number": 1, "sequenceNumber": 1,
            "type.text": "Jump Shot",
            "team.id": str(AWAY_ID),
            "homeTeamId": HOME_ID, "awayTeamId": AWAY_ID,
            "participants.0.athlete.id": str(ghost),
            "participants.1.athlete.id": None,
            "period.number": 1,
            "scoringPlay": False, "scoreValue": None,
            "shootingPlay": True, "pointsAttempted": 2,
            "clock.displayValue": "10:00",
        },
        {
            "game_play_number": 2, "sequenceNumber": 2,
            "type.text": "Layup Shot",
            "team.id": str(HOME_ID),
            "homeTeamId": HOME_ID, "awayTeamId": AWAY_ID,
            "participants.0.athlete.id": str(HOME_STARTERS[0]),
            "participants.1.athlete.id": None,
            "period.number": 1,
            "scoringPlay": True, "scoreValue": 2,
            "shootingPlay": True, "pointsAttempted": 2,
            "clock.displayValue": "9:50",
        },
    ]

    # Inject ghost into roster so player_team map knows it (triggers inv2 not inv1)
    rows_roster = _roster_df().to_dicts()
    rows_roster.append({"athlete_id": ghost, "team_id": AWAY_ID,
                        "starter": False, "active": True, "did_not_play": False})
    roster = pl.DataFrame(rows_roster)

    plays = pl.DataFrame(rows_plays)
    recon = reconstruct_lineups(plays, roster)
    recon_rows = recon.to_dicts()

    from my5.aggregate import (
        _tag_and_ones, _accumulate_game, _compute_league_avgs,
        _build_lineup_metrics,
    )
    lineup_acc: dict = {}
    player_acc: dict = {}
    fg_idxs, ft_idxs = _tag_and_ones(recon_rows)
    _accumulate_game(999, recon_rows, fg_idxs, ft_idxs, lineup_acc, player_acc, [])
    lm = _build_lineup_metrics(lineup_acc)

    # Inv2 drops the ghost row but keeps the home FG → home off_poss=1
    home_poss = lm.filter(pl.col("team_id") == HOME_ID)["total_off_poss"].sum()
    assert home_poss == 1, (
        f"inv2_participant drops the row only; clean FG in same stint must count. Got {home_poss}"
    )


def test_below_threshold_player_gets_shrinkage():
    """
    A player with 0 possessions on floor gets shrinkage_weight=0 and blended
    rate equals the league average.

    We verify the property via shrinkage formula logic rather than a full game run
    (the formula is deterministic given w=0).
    """
    from my5.aggregate import _shrink
    blended, w = _shrink(observed=0.5, n=0, league_avg=0.25, prior_n=50)
    assert w == 0.0
    assert blended == pytest.approx(0.25)


def test_shrinkage_weight_above_threshold():
    """
    A player with n=50 possessions and prior_n=50 gets shrinkage_weight=0.5
    (half from own data, half from league average).
    """
    from my5.aggregate import _shrink
    _, w = _shrink(observed=0.30, n=50, league_avg=0.20, prior_n=50)
    assert w == pytest.approx(0.5)
