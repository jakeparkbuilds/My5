"""
Lineup reconstruction from ESPN play-by-play.

Takes a sorted PBP DataFrame and a game-roster DataFrame (from espn_nba_game_rosters)
and returns the PBP with three new columns attached:

  home_lineup   list[int]  — sorted list of 5 home athlete IDs on the floor at event start
  away_lineup   list[int]  — sorted list of 5 away athlete IDs on the floor at event start
  lineup_valid  bool       — False if either invariant was violated for this row

Invariant 1 (count): each team's lineup must have exactly 5 players after every sub.
Invariant 2 (participant): for non-sub events, the acting team's participant(s) must be
members of that team's reconstructed lineup at that moment.

On any violation, we flag the row and log it — we never fabricate a removal or guess.
"""

from __future__ import annotations

import logging
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

_SUB_TYPE = "Substitution"
_END_TYPES = {"End Period", "End Game"}


def reconstruct_lineups(
    plays: pl.DataFrame,
    roster: pl.DataFrame,
) -> pl.DataFrame:
    """Attach home_lineup, away_lineup, lineup_valid columns to a single-game PBP DataFrame.

    Args:
        plays:  PBP rows for ONE game, from espn_nba_pbp / load_nba_pbp.
        roster: Game roster from espn_nba_game_rosters for the same game_id.

    Returns:
        plays with three additional columns appended.
    """
    home_team_id, away_team_id = _infer_home_away_ids(plays)
    player_team: dict[int, int] = _build_player_team_map(roster, home_team_id, away_team_id)

    home_starters, away_starters = _get_starters(roster, home_team_id, away_team_id)

    plays = _sort_plays(plays)

    home_lineups: list[list[int]] = []
    away_lineups: list[list[int]] = []
    valid_flags: list[bool] = []

    home: set[int] = set(home_starters)
    away: set[int] = set(away_starters)

    rows: list[dict[str, Any]] = plays.to_dicts()

    for row in rows:
        gpn: int = row["game_play_number"]
        event_type: str = row.get("type.text") or ""
        is_sub = event_type == _SUB_TYPE

        if is_sub:
            # Stamp pre-sub state for substitution rows too (the swap hasn't fired yet)
            home_lineups.append(sorted(home))
            away_lineups.append(sorted(away))

            valid = _apply_sub(row, home, away, player_team, home_team_id, away_team_id, gpn)
            valid_flags.append(valid)
        else:
            # Stamp current lineup (pre-event)
            home_lineups.append(sorted(home))
            away_lineups.append(sorted(away))

            valid = _check_participant_invariant(
                row, home, away, player_team, home_team_id, away_team_id, gpn
            )
            valid_flags.append(valid)

    return plays.with_columns(
        pl.Series("home_lineup", home_lineups, dtype=pl.List(pl.Int64)),
        pl.Series("away_lineup", away_lineups, dtype=pl.List(pl.Int64)),
        pl.Series("lineup_valid", valid_flags, dtype=pl.Boolean),
    )


# ── internal helpers ──────────────────────────────────────────────────────────


def _sort_plays(plays: pl.DataFrame) -> pl.DataFrame:
    """Sort by game_play_number, with sequenceNumber then row-index as tiebreakers.

    game_play_number is unique in every game we have observed, but sequenceNumber
    is NOT globally monotonic (ESPN emits events out of sequence within tight
    clock windows). We sort by game_play_number as the primary key and add
    tiebreakers defensively for games where duplicates might appear.
    """
    plays = plays.with_row_index("_row_idx")
    plays = plays.sort(["game_play_number", "sequenceNumber", "_row_idx"])
    return plays.drop("_row_idx")


def _infer_home_away_ids(plays: pl.DataFrame) -> tuple[int, int]:
    """Read homeTeamId / awayTeamId from the first non-null row."""
    row = plays.select(["homeTeamId", "awayTeamId"]).drop_nulls().row(0)
    return int(row[0]), int(row[1])


def _build_player_team_map(
    roster: pl.DataFrame,
    home_team_id: int,
    away_team_id: int,
) -> dict[int, int]:
    """Map athlete_id → team_id for every rostered player in this game.

    We intentionally do NOT filter on active=True. ESPN's active flag reflects
    contract/eligibility status, not "dressed tonight" — players on hardship
    exceptions, two-way contracts, and late additions show active=False yet
    appear in substitution events. We exclude only explicit DNPs so genuine
    scratches don't pollute the map.
    """
    eligible = roster.filter(
        pl.col("team_id").is_in([home_team_id, away_team_id])
        & pl.col("did_not_play").eq(False)
    )
    return {
        int(r["athlete_id"]): int(r["team_id"])
        for r in eligible.select(["athlete_id", "team_id"]).to_dicts()
    }


def _get_starters(
    roster: pl.DataFrame,
    home_team_id: int,
    away_team_id: int,
) -> tuple[list[int], list[int]]:
    """Return (home_starters, away_starters) from the starter=True roster rows."""
    starters = roster.filter(pl.col("starter").eq(True))
    home = sorted(
        starters.filter(pl.col("team_id").eq(home_team_id))["athlete_id"]
        .cast(pl.Int64)
        .to_list()
    )
    away = sorted(
        starters.filter(pl.col("team_id").eq(away_team_id))["athlete_id"]
        .cast(pl.Int64)
        .to_list()
    )
    if len(home) != 5:
        raise ValueError(
            f"Expected 5 home starters (team_id={home_team_id}), got {len(home)}: {home}"
        )
    if len(away) != 5:
        raise ValueError(
            f"Expected 5 away starters (team_id={away_team_id}), got {len(away)}: {away}"
        )
    return home, away


def _apply_sub(
    row: dict[str, Any],
    home: set[int],
    away: set[int],
    player_team: dict[int, int],
    home_team_id: int,
    away_team_id: int,
    gpn: int,
) -> bool:
    """Apply one substitution to the mutable lineup sets. Returns lineup_valid."""
    raw_in = row.get("participants.0.athlete.id")
    raw_out = row.get("participants.1.athlete.id")

    if raw_in is None or raw_out is None:
        logger.warning(
            "gpn=%d: substitution row missing participant IDs — skipping", gpn
        )
        return False

    player_in = int(raw_in)
    player_out = int(raw_out)

    team_id = player_team.get(player_in)
    if team_id is None:
        logger.warning(
            "gpn=%d: player_in=%d not in roster player_team map — cannot route sub",
            gpn, player_in,
        )
        return False

    lineup = home if team_id == home_team_id else away
    label = "home" if team_id == home_team_id else "away"

    valid = True

    if player_out not in lineup:
        logger.warning(
            "gpn=%d: INVARIANT 1 violation — player_out=%d not in %s lineup %s; "
            "flagging row, NOT modifying lineup",
            gpn, player_out, label, sorted(lineup),
        )
        valid = False
        # Do not apply the swap — state is already corrupted; just add player_in
        # so subsequent events have a chance at being correct, but keep valid=False.
        lineup.add(player_in)
    else:
        lineup.discard(player_out)
        lineup.add(player_in)

    # Count invariant check after attempted swap
    if len(lineup) != 5:
        logger.warning(
            "gpn=%d: INVARIANT 1 violation — %s lineup has %d players after sub: %s",
            gpn, label, len(lineup), sorted(lineup),
        )
        valid = False

    return valid


def _check_participant_invariant(
    row: dict[str, Any],
    home: set[int],
    away: set[int],
    player_team: dict[int, int],
    home_team_id: int,
    away_team_id: int,
    gpn: int,
) -> bool:
    """Invariant 2: acting team's participant(s) must be in that team's lineup."""
    event_type = row.get("type.text") or ""
    if event_type in _END_TYPES:
        # Boundary markers have no participant; skip check
        return True

    acting_team_raw = row.get("team.id")
    if acting_team_raw is None:
        return True  # no team attribution (e.g. jump ball handled separately)

    acting_team = int(acting_team_raw)
    lineup = home if acting_team == home_team_id else away

    valid = True
    for key in ("participants.0.athlete.id", "participants.1.athlete.id"):
        raw = row.get(key)
        if raw is None:
            continue
        pid = int(raw)
        p_team = player_team.get(pid)
        # Only check participants who belong to the acting team
        if p_team != acting_team:
            continue
        if pid not in lineup:
            logger.warning(
                "gpn=%d: INVARIANT 2 violation — participant %d (event='%s') "
                "not in %s lineup %s",
                gpn, pid, event_type,
                "home" if acting_team == home_team_id else "away",
                sorted(lineup),
            )
            valid = False

    return valid
