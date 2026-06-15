"""
Aggregation stage (P1): turn reconstructed lineup events into
  (a) per-lineup metrics for the frontend + lineup-level defense
  (b) per-player parameters for the P2 simulator

Public API
----------
    run_aggregation(game_ids) → (lineup_metrics_df, player_params_df)

Design decisions (see DECISIONS.md for full rationale):
  - Possessions counted by event-walking (not Hollinger formula)
  - And-one pairing: semantic player+team+clock match, no row-window heuristic
  - Rim classification: event type-name primary (Layup/Dunk/Tip/Finger Roll keywords)
  - Usage rate: (FGA + TOV + FT_trips) / team_poss_on_floor
  - OREB%: player_ORs / (team missed FGA + team missed final FT while on floor)
  - Exclusion: inv1_count → drop stint; inv2_participant → drop row only
  - Shrinkage: data-derived league average; prior_n=50 for rates, 25 for zone FG%
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import polars as pl

from my5.loader import load_pbp, load_roster
from my5.reconstruct import reconstruct_lineups

# ── Constants ─────────────────────────────────────────────────────────────────

# Rim classification: 2PT shots whose type.text contains any of these → "rim"
# "Hook" covers Hook Shot, Turnaround Hook Shot, Driving Hook Shot — all 88-95%
# at y≤7 (median y=4-6) in our 52-game corpus. Floaters stay mid: they split
# ~50/50 at the rim-area boundary and are conventionally mid-range.
_RIM_KEYWORDS = frozenset({"Layup", "Dunk", "Tip", "Finger Roll", "Hook"})

# Regex to detect "N of N" in a Free Throw type string (final FT in sequence)
_FINAL_FT_RE = re.compile(r"(\d+) of (\d+)")

# Possession-floor thresholds for rates / zone FG%
_POSS_FLOOR = 50
_ZONE_FLOOR = 25

# Shrinkage prior strengths (number of "pseudo-observations" toward the league avg)
_PRIOR_N_RATE = 50
_PRIOR_N_ZONE = 25


# ── Shot-zone helpers ─────────────────────────────────────────────────────────


def _shot_zone(points_attempted: int | None, type_text: str) -> str | None:
    """Return 'rim', 'mid', or 'three'. None if not a classifiable FGA."""
    if points_attempted == 3:
        return "three"
    if points_attempted == 2:
        for kw in _RIM_KEYWORDS:
            if kw in type_text:
                return "rim"
        return "mid"
    return None


# ── Free-throw helpers ────────────────────────────────────────────────────────


def _is_final_ft(type_text: str) -> bool:
    """True if this FT is the last in its trip (e.g. '2 of 2', '3 of 3', '1 of 1')."""
    m = _FINAL_FT_RE.search(type_text)
    return bool(m and m.group(1) == m.group(2))


def _is_ft_trip_start(type_text: str) -> bool:
    """True if this FT event is the first in a trip (matches '1 of N' in the name)."""
    return bool(_FINAL_FT_RE.search(type_text)) and "1 of" in type_text


# ── And-one precomputation ────────────────────────────────────────────────────


def _tag_and_ones(rows: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    """
    Return (and_one_fg_indices, and_one_ft_indices) for a game's row list.

    An and-one is a made FG (scoreValue in {2,3}) immediately followed (within
    15 rows) by a Free Throw - 1 of 1 where:
      - participants.0.athlete.id matches the FG scorer
      - team.id matches the FG team
      - clock.displayValue matches (same stoppage moment)
    """
    and_one_fg: set[int] = set()
    and_one_ft: set[int] = set()

    for i, row in enumerate(rows):
        if not row.get("scoringPlay"):
            continue
        score_val = row.get("scoreValue")
        if score_val not in (2, 3):
            continue
        # This is a made 2PT or 3PT. Scan forward for matching FT-1-of-1.
        scorer = row.get("participants.0.athlete.id")
        team = row.get("team.id")
        clock = row.get("clock.displayValue")

        for j in range(i + 1, min(i + 16, len(rows))):
            r2 = rows[j]
            if r2.get("type.text") == "Free Throw - 1 of 1":
                if (
                    r2.get("participants.0.athlete.id") == scorer
                    and r2.get("team.id") == team
                    and r2.get("clock.displayValue") == clock
                ):
                    and_one_fg.add(i)
                    and_one_ft.add(j)
                    break

    return and_one_fg, and_one_ft


# ── Default accumulators ──────────────────────────────────────────────────────


def _new_player_acc() -> dict[str, Any]:
    return {
        "team_poss_on_floor": 0,
        "fga": 0, "fgm": 0,
        "rim_a": 0, "rim_m": 0,
        "mid_a": 0, "mid_m": 0,
        "fg3a": 0, "fg3m": 0,
        "tov": 0,
        "ft_trips": 0,
        "fta": 0, "ftm": 0,
        "oreb": 0,
        "oreb_opp": 0,
        "game_ids": set(),
    }


def _new_lineup_acc() -> dict[str, Any]:
    return {
        "off_poss": 0,
        "def_poss": 0,
        "pts": 0,
        "pts_allowed": 0,
        # Opponent shot attempts while this lineup defends
        "opp_rim_a": 0, "opp_rim_m": 0,
        "opp_mid_a": 0, "opp_mid_m": 0,
        "opp_3p_a": 0, "opp_3p_m": 0,
        # Defensive misc
        "forced_to": 0,
        "dreb": 0,
        "dreb_opp": 0,  # opponent missed FGA + opponent missed final FT
        "game_ids": set(),
    }


# ── Core per-game accumulation ────────────────────────────────────────────────


def _accumulate_game(
    game_id: int,
    recon_rows: list[dict[str, Any]],
    and_one_fg_idxs: set[int],
    and_one_ft_idxs: set[int],
    lineup_acc: dict[tuple, dict],
    player_acc: dict[int, dict],
    corrupted_stints: list[dict],
) -> None:
    """Walk one game's reconstructed rows and accumulate lineup + player stats."""

    # Annotate each row with and-one flags and a stint_id.
    for i, row in enumerate(recon_rows):
        row["_is_and_one_fg"] = i in and_one_fg_idxs
        row["_is_and_one_ft"] = i in and_one_ft_idxs

    # Stint segmentation: maximal runs of identical (home_lineup, away_lineup).
    stint_id = 0
    prev_key: tuple | None = None
    for row in recon_rows:
        key = (tuple(row["home_lineup"]), tuple(row["away_lineup"]))
        if key != prev_key:
            stint_id += 1
            prev_key = key
        row["_stint_id"] = stint_id

    # Find stints contaminated by inv1_count → exclude entirely.
    inv1_stints: set[int] = {
        row["_stint_id"]
        for row in recon_rows
        if row.get("violation_type") == "inv1_count"
    }

    # Build filtered view: drop inv1 stints entirely, drop inv2 rows only.
    valid_rows = [
        row
        for row in recon_rows
        if row["_stint_id"] not in inv1_stints
        and row.get("violation_type") != "inv2_participant"
    ]

    # Group into stints for processing.
    stints: dict[int, list[dict]] = defaultdict(list)
    for row in valid_rows:
        stints[row["_stint_id"]].append(row)

    home_team_id = int(recon_rows[0]["homeTeamId"])
    away_team_id = int(recon_rows[0]["awayTeamId"])

    for sid, rows in stints.items():
        if not rows:
            continue

        home_lineup_tuple = tuple(rows[0]["home_lineup"])
        away_lineup_tuple = tuple(rows[0]["away_lineup"])
        home_lineup_set = set(home_lineup_tuple)
        away_lineup_set = set(away_lineup_tuple)

        # Skip stints with a corrupted lineup (e.g. 6 players after an inv1 sub
        # added player_in without removing anyone). An inv1 violation makes the
        # violating stint's key invalid, but the following stint also carries the
        # dirty lineup — both must be excluded.
        # NOT silent: every skip is logged to corrupted_stints for the run report.
        if len(home_lineup_set) != 5 or len(away_lineup_set) != 5:
            gpns = [r.get("game_play_number") for r in rows]
            corrupted_stints.append({
                "game_id": game_id,
                "stint_id": sid,
                "home_size": len(home_lineup_set),
                "away_size": len(away_lineup_set),
                "first_gpn": gpns[0] if gpns else None,
                "last_gpn": gpns[-1] if gpns else None,
                "row_count": len(rows),
            })
            continue

        home_key = (home_lineup_tuple, home_team_id)
        away_key = (away_lineup_tuple, away_team_id)

        if home_key not in lineup_acc:
            lineup_acc[home_key] = _new_lineup_acc()
        if away_key not in lineup_acc:
            lineup_acc[away_key] = _new_lineup_acc()

        home_lm = lineup_acc[home_key]
        away_lm = lineup_acc[away_key]
        home_lm["game_ids"].add(game_id)
        away_lm["game_ids"].add(game_id)

        for pid in home_lineup_set:
            if pid not in player_acc:
                player_acc[pid] = _new_player_acc()
            player_acc[pid]["game_ids"].add(game_id)
        for pid in away_lineup_set:
            if pid not in player_acc:
                player_acc[pid] = _new_player_acc()
            player_acc[pid]["game_ids"].add(game_id)

        # Walk rows and count possession ends + stats.
        for row in rows:
            team_raw = row.get("team.id")
            type_text = row.get("type.text") or ""
            scoring = bool(row.get("scoringPlay"))
            score_val = row.get("scoreValue")
            shooting = bool(row.get("shootingPlay"))
            pts_att = row.get("pointsAttempted")
            p0_raw = row.get("participants.0.athlete.id")
            is_and_one_fg = row["_is_and_one_fg"]
            is_and_one_ft = row["_is_and_one_ft"]

            team_id = int(team_raw) if team_raw is not None else None
            p0 = int(p0_raw) if p0_raw is not None else None

            is_home = (team_id == home_team_id)
            is_away = (team_id == away_team_id)

            # ── Possession-end detection ──────────────────────────────────
            poss_end_team: int | None = None

            if scoring and score_val in (2, 3) and not is_and_one_fg:
                # Made FG, not and-one → ball goes to other team
                poss_end_team = team_id

            elif is_and_one_ft:
                # And-one FT completes the possession
                poss_end_team = team_id

            elif (
                "Free Throw" in type_text
                and "Technical" not in type_text
                and _is_final_ft(type_text)
                and not is_and_one_ft
                and scoring  # made final FT only; missed → wait for DREB
            ):
                poss_end_team = team_id

            elif type_text == "Defensive Rebound":
                # Rebounding team gained ball → other team's possession just ended
                poss_end_team = away_team_id if is_home else home_team_id

            elif "Turnover" in type_text:
                poss_end_team = team_id

            if poss_end_team == home_team_id:
                home_lm["off_poss"] += 1
                away_lm["def_poss"] += 1
                for pid in home_lineup_set:
                    player_acc[pid]["team_poss_on_floor"] += 1
            elif poss_end_team == away_team_id:
                away_lm["off_poss"] += 1
                home_lm["def_poss"] += 1
                for pid in away_lineup_set:
                    player_acc[pid]["team_poss_on_floor"] += 1

            # ── Scoring ───────────────────────────────────────────────────
            if scoring and score_val and team_id is not None:
                if is_home:
                    home_lm["pts"] += int(score_val)
                    away_lm["pts_allowed"] += int(score_val)
                elif is_away:
                    away_lm["pts"] += int(score_val)
                    home_lm["pts_allowed"] += int(score_val)

            # ── Shot stats ────────────────────────────────────────────────
            if shooting and pts_att in (2, 3) and p0 is not None and team_id is not None:
                zone = _shot_zone(pts_att, type_text)
                made = scoring
                p_acc = player_acc.get(p0)
                if p_acc is not None:
                    p_acc["fga"] += 1
                    if made:
                        p_acc["fgm"] += 1
                    if zone == "rim":
                        p_acc["rim_a"] += 1
                        if made:
                            p_acc["rim_m"] += 1
                    elif zone == "mid":
                        p_acc["mid_a"] += 1
                        if made:
                            p_acc["mid_m"] += 1
                    elif zone == "three":
                        p_acc["fg3a"] += 1
                        if made:
                            p_acc["fg3m"] += 1

                # Opponent shot tracking for lineup defense
                if is_home:
                    # Home team shooting → accumulates in away lineup's defensive stats
                    _accum_opp_shot(away_lm, zone, made)
                elif is_away:
                    _accum_opp_shot(home_lm, zone, made)

                # OREB opportunity: if the shot is MISSED, each of the 5 shooters'
                # teammates on the floor gets an OREB opportunity.
                if not made:
                    opp_lineup = away_lineup_set if is_home else home_lineup_set
                    for pid in (home_lineup_set if is_home else away_lineup_set):
                        p_acc2 = player_acc.get(pid)
                        if p_acc2 is not None:
                            p_acc2["oreb_opp"] += 1
                    # Opponent's defensive rebound opportunity
                    for pid in opp_lineup:
                        p_acc2 = player_acc.get(pid)
                        if p_acc2 is not None:
                            pass  # DREB tracked by the DREB event below

            # ── Missed final FT → OREB opportunity ───────────────────────
            if (
                "Free Throw" in type_text
                and "Technical" not in type_text
                and _is_final_ft(type_text)
                and not scoring  # missed
                and not is_and_one_ft
                and team_id is not None
            ):
                for pid in (home_lineup_set if is_home else away_lineup_set):
                    p_acc2 = player_acc.get(pid)
                    if p_acc2 is not None:
                        p_acc2["oreb_opp"] += 1
                # Also count for lineup defense (defensive DREB opp)
                if is_home:
                    away_lm["dreb_opp"] += 1
                elif is_away:
                    home_lm["dreb_opp"] += 1

            # ── Missed FGA → lineup dreb_opp ─────────────────────────────
            if shooting and pts_att in (2, 3) and not scoring and team_id is not None:
                if is_home:
                    away_lm["dreb_opp"] += 1
                elif is_away:
                    home_lm["dreb_opp"] += 1

            # ── Turnovers ─────────────────────────────────────────────────
            if "Turnover" in type_text and p0 is not None:
                p_acc = player_acc.get(p0)
                if p_acc is not None:
                    p_acc["tov"] += 1
                if is_home:
                    away_lm["forced_to"] += 1
                elif is_away:
                    home_lm["forced_to"] += 1

            # ── FT trips (usage numerator) ────────────────────────────────
            if (
                "Free Throw" in type_text
                and "Technical" not in type_text
                and _is_ft_trip_start(type_text)
                and not is_and_one_ft
                and p0 is not None
            ):
                p_acc = player_acc.get(p0)
                if p_acc is not None:
                    p_acc["ft_trips"] += 1

            # ── FT make/miss totals (all FTs for FT%) ────────────────────
            if (
                "Free Throw" in type_text
                and "Technical" not in type_text
                and p0 is not None
            ):
                p_acc = player_acc.get(p0)
                if p_acc is not None:
                    p_acc["fta"] += 1
                    if scoring:
                        p_acc["ftm"] += 1

            # ── Offensive Rebounds ────────────────────────────────────────
            if type_text == "Offensive Rebound" and p0 is not None:
                p_acc = player_acc.get(p0)
                if p_acc is not None:
                    p_acc["oreb"] += 1

            # ── Defensive Rebounds (player + lineup) ─────────────────────
            if type_text == "Defensive Rebound":
                if p0 is not None:
                    pass  # per-player DREB not needed for current params
                if is_home:
                    home_lm["dreb"] += 1
                elif is_away:
                    away_lm["dreb"] += 1


def _accum_opp_shot(
    def_lineup: dict,
    zone: str | None,
    made: bool,
) -> None:
    """Add one opponent shot to the defending lineup's defensive shot counters."""
    if zone == "rim":
        def_lineup["opp_rim_a"] += 1
        if made:
            def_lineup["opp_rim_m"] += 1
    elif zone == "mid":
        def_lineup["opp_mid_a"] += 1
        if made:
            def_lineup["opp_mid_m"] += 1
    elif zone == "three":
        def_lineup["opp_3p_a"] += 1
        if made:
            def_lineup["opp_3p_m"] += 1


# ── Shrinkage ─────────────────────────────────────────────────────────────────


def _shrink(observed: float, n: int, league_avg: float, prior_n: int) -> tuple[float, float]:
    """
    Return (blended_rate, shrinkage_weight).

    shrinkage_weight = n / (n + prior_n)
      - weight=0 → full league avg (n=0)
      - weight=1 → full observed rate (n >> prior_n)
    """
    w = n / (n + prior_n)
    return observed * w + league_avg * (1 - w), w


# ── League-average computation (data-derived, not hard-coded) ─────────────────


def _compute_league_avgs(
    player_acc: dict[int, dict],
) -> dict[str, float]:
    """
    Compute volume-weighted league averages from all players in the data slice.
    These become the shrinkage prior — no hard-coded constants.
    """
    totals: dict[str, int] = defaultdict(int)

    for acc in player_acc.values():
        poss = acc["team_poss_on_floor"]
        usage_num = acc["fga"] + acc["tov"] + acc["ft_trips"]
        totals["usage_num"] += usage_num
        totals["usage_den"] += poss

        totals["rim_m"] += acc["rim_m"]
        totals["rim_a"] += acc["rim_a"]
        totals["mid_m"] += acc["mid_m"]
        totals["mid_a"] += acc["mid_a"]
        totals["fg3m"] += acc["fg3m"]
        totals["fg3a"] += acc["fg3a"]

        totals["tov_num"] += acc["tov"]
        totals["tov_den"] += usage_num

        totals["ft_trip_num"] += acc["ft_trips"]
        totals["ft_poss_den"] += poss

        totals["ftm"] += acc["ftm"]
        totals["fta"] += acc["fta"]

        totals["oreb"] += acc["oreb"]
        totals["oreb_opp"] += acc["oreb_opp"]

    def _safe_div(n: int, d: int) -> float:
        return n / d if d > 0 else 0.0

    return {
        "usage_rate": _safe_div(totals["usage_num"], totals["usage_den"]),
        "rim_fg_pct": _safe_div(totals["rim_m"], totals["rim_a"]),
        "mid_fg_pct": _safe_div(totals["mid_m"], totals["mid_a"]),
        "fg3_pct": _safe_div(totals["fg3m"], totals["fg3a"]),
        "tov_rate": _safe_div(totals["tov_num"], totals["tov_den"]),
        "ft_rate": _safe_div(totals["ft_trip_num"], totals["ft_poss_den"]),
        "ft_pct": _safe_div(totals["ftm"], totals["fta"]),
        "oreb_rate": _safe_div(totals["oreb"], totals["oreb_opp"]),
    }


# ── Output table builders ─────────────────────────────────────────────────────


def _build_lineup_metrics(lineup_acc: dict[tuple, dict]) -> pl.DataFrame:
    def _safe(n: int, d: int) -> float:
        return n / d if d > 0 else 0.0

    rows = []
    for (lineup_tuple, team_id), acc in lineup_acc.items():
        off = acc["off_poss"]
        defs = acc["def_poss"]
        off_rtg = _safe(acc["pts"], off) * 100
        def_rtg = _safe(acc["pts_allowed"], defs) * 100
        rows.append({
            "lineup": list(lineup_tuple),
            "team_id": team_id,
            "games_observed": len(acc["game_ids"]),
            "total_off_poss": off,
            "total_def_poss": defs,
            "pts_scored": acc["pts"],
            "pts_allowed": acc["pts_allowed"],
            "off_rating": round(off_rtg, 1),
            "def_rating": round(def_rtg, 1),
            "net_rating": round(off_rtg - def_rtg, 1),
            "opp_rim_fga": acc["opp_rim_a"],
            "opp_rim_fgm": acc["opp_rim_m"],
            "opp_mid_fga": acc["opp_mid_a"],
            "opp_mid_fgm": acc["opp_mid_m"],
            "opp_3p_fga": acc["opp_3p_a"],
            "opp_3p_fgm": acc["opp_3p_m"],
            "opp_rim_fg_pct": round(_safe(acc["opp_rim_m"], acc["opp_rim_a"]), 3),
            "opp_mid_fg_pct": round(_safe(acc["opp_mid_m"], acc["opp_mid_a"]), 3),
            "opp_3p_fg_pct": round(_safe(acc["opp_3p_m"], acc["opp_3p_a"]), 3),
            "forced_to": acc["forced_to"],
            "forced_to_rate": round(_safe(acc["forced_to"], defs), 3),
            "dreb": acc["dreb"],
            "dreb_opp": acc["dreb_opp"],
            "dreb_rate": round(_safe(acc["dreb"], acc["dreb_opp"]), 3),
        })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).sort("total_off_poss", descending=True)


def _build_player_params(
    player_acc: dict[int, dict],
    league_avgs: dict[str, float],
) -> pl.DataFrame:
    def _safe(n: int, d: int) -> float:
        return n / d if d > 0 else 0.0

    rows = []
    for pid, acc in player_acc.items():
        poss = acc["team_poss_on_floor"]
        usage_num = acc["fga"] + acc["tov"] + acc["ft_trips"]
        fga = acc["fga"]

        # Raw rates
        usage_raw = _safe(usage_num, poss)
        rim_pct_raw = _safe(acc["rim_m"], acc["rim_a"])
        mid_pct_raw = _safe(acc["mid_m"], acc["mid_a"])
        fg3_pct_raw = _safe(acc["fg3m"], acc["fg3a"])
        tov_raw = _safe(acc["tov"], usage_num)
        ft_rate_raw = _safe(acc["ft_trips"], poss)
        ft_pct_raw = _safe(acc["ftm"], acc["fta"])
        oreb_raw = _safe(acc["oreb"], acc["oreb_opp"])
        rim_rate = _safe(acc["rim_a"], fga)
        mid_rate = _safe(acc["mid_a"], fga)
        fg3_rate = _safe(acc["fg3a"], fga)

        # Shrinkage
        usage, usage_w = _shrink(usage_raw, poss, league_avgs["usage_rate"], _PRIOR_N_RATE)
        rim_pct, rim_pct_w = _shrink(rim_pct_raw, acc["rim_a"], league_avgs["rim_fg_pct"], _PRIOR_N_ZONE)
        mid_pct, mid_pct_w = _shrink(mid_pct_raw, acc["mid_a"], league_avgs["mid_fg_pct"], _PRIOR_N_ZONE)
        fg3_pct, fg3_pct_w = _shrink(fg3_pct_raw, acc["fg3a"], league_avgs["fg3_pct"], _PRIOR_N_ZONE)
        tov_rate, tov_rate_w = _shrink(tov_raw, usage_num, league_avgs["tov_rate"], _PRIOR_N_RATE)
        ft_rate, ft_rate_w = _shrink(ft_rate_raw, poss, league_avgs["ft_rate"], _PRIOR_N_RATE)
        ft_pct, ft_pct_w = _shrink(ft_pct_raw, acc["fta"], league_avgs["ft_pct"], _PRIOR_N_ZONE)
        oreb_rate, oreb_rate_w = _shrink(oreb_raw, acc["oreb_opp"], league_avgs["oreb_rate"], _PRIOR_N_RATE)

        rows.append({
            "athlete_id": pid,
            "games": len(acc["game_ids"]),
            "team_poss_on_floor": poss,
            "fga": fga,
            "fgm": acc["fgm"],
            "rim_a": acc["rim_a"],
            "rim_m": acc["rim_m"],
            "mid_a": acc["mid_a"],
            "mid_m": acc["mid_m"],
            "fg3a": acc["fg3a"],
            "fg3m": acc["fg3m"],
            "tov": acc["tov"],
            "ft_trips": acc["ft_trips"],
            "fta": acc["fta"],
            "ftm": acc["ftm"],
            "oreb": acc["oreb"],
            "oreb_opp": acc["oreb_opp"],
            # Shot distribution
            "shot_rim_rate": round(rim_rate, 3),
            "shot_mid_rate": round(mid_rate, 3),
            "shot_3p_rate": round(fg3_rate, 3),
            # Raw rates (before shrinkage)
            "usage_rate_raw": round(usage_raw, 4),
            "rim_fg_pct_raw": round(rim_pct_raw, 4),
            "mid_fg_pct_raw": round(mid_pct_raw, 4),
            "fg3_pct_raw": round(fg3_pct_raw, 4),
            "tov_rate_raw": round(tov_raw, 4),
            "ft_rate_raw": round(ft_rate_raw, 4),
            "ft_pct_raw": round(ft_pct_raw, 4),
            "oreb_rate_raw": round(oreb_raw, 4),
            # Shrinkage weights (0 = full prior, 1 = full observed)
            "usage_shrink_wt": round(usage_w, 3),
            "rim_pct_shrink_wt": round(rim_pct_w, 3),
            "mid_pct_shrink_wt": round(mid_pct_w, 3),
            "fg3_pct_shrink_wt": round(fg3_pct_w, 3),
            "tov_shrink_wt": round(tov_rate_w, 3),
            "ft_rate_shrink_wt": round(ft_rate_w, 3),
            "ft_pct_shrink_wt": round(ft_pct_w, 3),
            "oreb_shrink_wt": round(oreb_rate_w, 3),
            # Blended (shrinkage-adjusted) rates — these are the simulator inputs
            "usage_rate": round(usage, 4),
            "rim_fg_pct": round(rim_pct, 4),
            "mid_fg_pct": round(mid_pct, 4),
            "fg3_pct": round(fg3_pct, 4),
            "tov_rate": round(tov_rate, 4),
            "ft_rate": round(ft_rate, 4),
            "ft_pct": round(ft_pct, 4),
            "oreb_rate": round(oreb_rate, 4),
        })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).sort("team_poss_on_floor", descending=True)


# ── Public entry point ────────────────────────────────────────────────────────


def run_aggregation(
    game_ids: list[int],
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict]]:
    """
    Run full aggregation over the provided game IDs.

    Returns
    -------
    lineup_metrics : pl.DataFrame
        One row per (lineup, team_id) pair with offensive and defensive metrics.
    player_params : pl.DataFrame
        One row per athlete_id with usage, shot-type, and efficiency parameters.
    corrupted_stints : list[dict]
        Every stint skipped because its lineup had ≠ 5 players on either side.
        Keys: game_id, stint_id, home_size, away_size, first_gpn, last_gpn, row_count.
        Should be empty on clean data; non-zero count at full-season scale is a signal
        to investigate data quality.
    """
    lineup_acc: dict[tuple, dict] = {}
    player_acc: dict[int, dict] = {}
    corrupted_stints: list[dict] = []

    for game_id in game_ids:
        plays = load_pbp(game_id)
        roster = load_roster(game_id)
        recon = reconstruct_lineups(plays, roster)
        rows = recon.to_dicts()

        and_one_fg_idxs, and_one_ft_idxs = _tag_and_ones(rows)
        _accumulate_game(
            game_id, rows, and_one_fg_idxs, and_one_ft_idxs,
            lineup_acc, player_acc, corrupted_stints,
        )

    league_avgs = _compute_league_avgs(player_acc)
    lineup_metrics = _build_lineup_metrics(lineup_acc)
    player_params = _build_player_params(player_acc, league_avgs)

    return lineup_metrics, player_params, corrupted_stints
