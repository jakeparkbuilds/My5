"""
Tests for src/my5/reconstruct.py.

Uses a minimal fake PBP + roster to avoid any network calls and give us
full control over what we're asserting. Known values are spot-checked
against the real Bulls @ 76ers game (game_id=401585087, 2024-01-03).

Real-game starters (from espn_nba_game_rosters, starter=True):
  PHI (home, team_id=20): 3416, 6440, 3059318, 3133603, 4431678
  CHI (away, team_id=4):  4431687, 6585, 3978, 2991350, 4395651

Real-game Q1 first sub (game_play_number=36, clock=8:28):
  CHI: Ayo Dosunmu (4397002) IN for Patrick Williams (4431687)
  After sub, CHI lineup: {4397002, 6585, 3978, 2991350, 4395651}

Real-game event at game_play_number=37 (CHI, DeMar DeRozan free throw):
  participant 3978 must be in away lineup after the sub above.
"""

import polars as pl
import pytest

from my5.reconstruct import reconstruct_lineups

# ── shared constants ──────────────────────────────────────────────────────────

HOME_ID = 20   # PHI
AWAY_ID = 4    # CHI

HOME_STARTERS = [3416, 6440, 3059318, 3133603, 4431678]
AWAY_STARTERS = [4431687, 6585, 3978, 2991350, 4395651]

# bench player used in sub tests
BENCH_PLAYER = 4397002   # Ayo Dosunmu (CHI)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_roster(extra_players: list[tuple[int, int]] | None = None) -> pl.DataFrame:
    """Minimal roster DataFrame with exactly the columns reconstruct uses."""
    rows = []
    for aid in HOME_STARTERS:
        rows.append({"athlete_id": aid, "team_id": HOME_ID, "starter": True, "active": True, "did_not_play": False})
    for aid in AWAY_STARTERS:
        rows.append({"athlete_id": aid, "team_id": AWAY_ID, "starter": True, "active": True, "did_not_play": False})
    # Bench player
    rows.append({"athlete_id": BENCH_PLAYER, "team_id": AWAY_ID, "starter": False, "active": True, "did_not_play": False})
    if extra_players:
        for aid, tid in extra_players:
            rows.append({"athlete_id": aid, "team_id": tid, "starter": False, "active": True, "did_not_play": False})
    return pl.DataFrame(rows)


def _base_row(gpn: int, event_type: str, team_id: int, p0: int | None = None, p1: int | None = None) -> dict:
    return {
        "game_play_number": gpn,
        "sequenceNumber": gpn,
        "type.text": event_type,
        "team.id": str(team_id),
        "homeTeamId": HOME_ID,
        "awayTeamId": AWAY_ID,
        "participants.0.athlete.id": str(p0) if p0 is not None else None,
        "participants.1.athlete.id": str(p1) if p1 is not None else None,
        "period.number": 1,
    }


def _make_sub_row(gpn: int, player_in: int, player_out: int, team_id: int) -> dict:
    return _base_row(gpn, "Substitution", team_id, p0=player_in, p1=player_out)


def _make_event_row(gpn: int, event_type: str, team_id: int, p0: int, p1: int | None = None) -> dict:
    return _base_row(gpn, event_type, team_id, p0=p0, p1=p1)


def _run(rows: list[dict]) -> pl.DataFrame:
    plays = pl.DataFrame(rows)
    roster = _make_roster()
    return reconstruct_lineups(plays, roster)


# ── tests: happy-path possessions ─────────────────────────────────────────────


def test_q1_opening_lineup_stamped_correctly():
    """Before any sub, every event should show the opening 5 starters."""
    rows = [
        _make_event_row(1, "Jump Shot", HOME_ID, p0=HOME_STARTERS[0]),
        _make_event_row(2, "Defensive Rebound", AWAY_ID, p0=AWAY_STARTERS[0]),
    ]
    result = _run(rows)

    row0 = result.row(0, named=True)
    assert row0["home_lineup"] == sorted(HOME_STARTERS)
    assert row0["away_lineup"] == sorted(AWAY_STARTERS)
    assert row0["lineup_valid"] is True

    row1 = result.row(1, named=True)
    assert row1["away_lineup"] == sorted(AWAY_STARTERS)
    assert row1["lineup_valid"] is True


def test_sub_updates_lineup_for_subsequent_event():
    """
    Sub at gpn=10: BENCH_PLAYER (4397002) IN for AWAY_STARTERS[0] (4431687).
    Event at gpn=11 should see the updated away lineup with BENCH_PLAYER in
    and AWAY_STARTERS[0] out.
    """
    player_out = AWAY_STARTERS[0]   # 4431687
    rows = [
        _make_sub_row(10, player_in=BENCH_PLAYER, player_out=player_out, team_id=AWAY_ID),
        _make_event_row(11, "Jump Shot", AWAY_ID, p0=AWAY_STARTERS[1]),  # Drummond (6585)
    ]
    result = _run(rows)

    # Sub row itself is stamped with PRE-sub lineup
    sub_row = result.row(0, named=True)
    assert player_out in sub_row["away_lineup"]
    assert BENCH_PLAYER not in sub_row["away_lineup"]
    assert sub_row["lineup_valid"] is True

    # Event row sees POST-sub lineup
    event_row = result.row(1, named=True)
    expected_away = sorted(
        [a for a in AWAY_STARTERS if a != player_out] + [BENCH_PLAYER]
    )
    assert event_row["away_lineup"] == expected_away
    assert event_row["lineup_valid"] is True


def test_home_lineup_unaffected_by_away_sub():
    """An away substitution must not touch the home lineup."""
    player_out = AWAY_STARTERS[0]
    rows = [
        _make_sub_row(10, player_in=BENCH_PLAYER, player_out=player_out, team_id=AWAY_ID),
        _make_event_row(11, "Jump Shot", HOME_ID, p0=HOME_STARTERS[0]),
    ]
    result = _run(rows)

    event_row = result.row(1, named=True)
    assert event_row["home_lineup"] == sorted(HOME_STARTERS)


def test_participant_in_lineup_passes_invariant2():
    """Event whose participant IS in the lineup should be lineup_valid=True."""
    rows = [_make_event_row(1, "Jump Shot", HOME_ID, p0=HOME_STARTERS[2])]  # Embiid
    result = _run(rows)
    assert result.row(0, named=True)["lineup_valid"] is True


# ── tests: invariant violations ───────────────────────────────────────────────


def test_sub_player_out_not_in_lineup_flags_invalid():
    """
    Constructed case: sub references a player_out who is not in the lineup.
    The row should be flagged lineup_valid=False and violation_type="inv1_count".
    """
    ghost_player_out = 9999999  # not in any lineup

    rows = [
        _make_sub_row(5, player_in=BENCH_PLAYER, player_out=ghost_player_out, team_id=AWAY_ID),
        _make_event_row(6, "Jump Shot", AWAY_ID, p0=AWAY_STARTERS[1]),
    ]
    plays = pl.DataFrame(rows)
    roster = _make_roster()
    result = reconstruct_lineups(plays, roster)

    sub_row = result.row(0, named=True)
    assert sub_row["lineup_valid"] is False, (
        "Sub with unknown player_out must set lineup_valid=False"
    )
    assert sub_row["violation_type"] == "inv1_count", (
        "Sub with unknown player_out must set violation_type='inv1_count'"
    )


def test_invariant2_flags_participant_not_in_lineup():
    """
    Constructed case: a non-sub event attributes an action to a player who,
    per our reconstructed state, is not in the lineup. Must flag lineup_valid=False
    and violation_type="inv2_participant".
    """
    ghost_participant = 9999999  # not in any lineup
    # Add ghost to the player_team map by injecting into roster
    extra_roster_row = (ghost_participant, AWAY_ID)

    rows = [_make_event_row(1, "Jump Shot", AWAY_ID, p0=ghost_participant)]
    plays = pl.DataFrame(rows)
    roster = _make_roster(extra_players=[extra_roster_row])
    result = reconstruct_lineups(plays, roster)

    row = result.row(0, named=True)
    assert row["lineup_valid"] is False, (
        "Event with participant not in lineup must set lineup_valid=False"
    )
    assert row["violation_type"] == "inv2_participant", (
        "Event with participant not in lineup must set violation_type='inv2_participant'"
    )


def test_clean_row_has_null_violation_type():
    """Clean rows must have violation_type=None (not a stale string)."""
    rows = [_make_event_row(1, "Jump Shot", HOME_ID, p0=HOME_STARTERS[0])]
    result = _run(rows)
    row = result.row(0, named=True)
    assert row["lineup_valid"] is True
    assert row["violation_type"] is None


def test_multiple_subs_same_team_apply_sequentially():
    """Two back-to-back subs for the same team must both take effect."""
    # This mirrors the real Q1 multi-sub sequence at 5:44
    bench2 = 4279815  # Terry Taylor (CHI bench)
    player_out1 = AWAY_STARTERS[0]  # Williams (4431687)
    player_out2 = AWAY_STARTERS[3]  # Caruso (2991350)

    rows = [
        _make_sub_row(10, player_in=BENCH_PLAYER, player_out=player_out1, team_id=AWAY_ID),
        _make_sub_row(11, player_in=bench2, player_out=player_out2, team_id=AWAY_ID),
        _make_event_row(12, "Jump Shot", AWAY_ID, p0=AWAY_STARTERS[1]),
    ]
    plays = pl.DataFrame(rows)
    roster = _make_roster(extra_players=[(bench2, AWAY_ID)])
    result = reconstruct_lineups(plays, roster)

    event_row = result.row(2, named=True)
    away = set(event_row["away_lineup"])
    assert BENCH_PLAYER in away
    assert bench2 in away
    assert player_out1 not in away
    assert player_out2 not in away
    assert len(away) == 5
    assert event_row["lineup_valid"] is True
