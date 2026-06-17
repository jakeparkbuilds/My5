"""
Phase A simulator validation: four tests against real historical data.

Run:  source .venv/bin/activate && python scripts/validate_simulator.py
Takes ~2-3 min (re-runs full 52-game aggregation).

Four tests (in priority order):
  1. SELF-MATCHUP SYMMETRY  — identical teams must produce mean margin ≈ 0.
     If this fails, the engine has a systematic bias; nothing downstream is trustworthy.
  2. OFF_RATING REPRODUCTION — simulated pts/100 vs league-average defense must be
     within ±8 of the lineup's real off_rating. Tests 7 lineups with ≥100 possessions.
  3. MONOTONE ORDERING — when one lineup's real net_rating exceeds another by ≥10,
     the simulator must predict it wins. Tests all qualifying pairs.
  4. DISTRIBUTION SANITY — 500 sims of league-avg vs league-avg:
     |mean| < 2, std 10–18, roughly symmetric.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from my5.aggregate import run_aggregation
from my5.simulator import (
    LeagueAverages,
    _POSS_PER_SIDE,
    make_league_avg_players,
    read_lineup_defense,
    simulate,
)

# ── Same 52-game slice used everywhere in P1 ─────────────────────────────────

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

_SEP = "=" * 70


# ── League-average computation from aggregation output ────────────────────────


def _safe_div(n: int | float, d: int | float) -> float:
    return n / d if d > 0 else 0.0


def compute_league_averages(
    lineup_rows: list[dict],
    player_rows: list[dict],
) -> LeagueAverages:
    """
    Compute attempt-weighted league averages from aggregation output dicts.

    These become the log5 baselines and shrinkage priors for the engine.
    Attempt-weighting is critical: a simple mean is dominated by 1-possession
    lineups and gives wildly wrong numbers.
    """
    # ── Player side ───────────────────────────────────────────────────────────
    total_fga      = sum(r["fga"]      for r in player_rows)
    total_rim_a    = sum(r["rim_a"]    for r in player_rows)
    total_rim_m    = sum(r["rim_m"]    for r in player_rows)
    total_mid_a    = sum(r["mid_a"]    for r in player_rows)
    total_mid_m    = sum(r["mid_m"]    for r in player_rows)
    total_fg3a     = sum(r["fg3a"]     for r in player_rows)
    total_fg3m     = sum(r["fg3m"]     for r in player_rows)
    total_tov      = sum(r["tov"]      for r in player_rows)
    total_ft_trips = sum(r["ft_trips"] for r in player_rows)
    total_poss     = sum(r["team_poss_on_floor"] for r in player_rows)
    total_usage_ev = sum(r["fga"] + r["tov"] + r["ft_trips"] for r in player_rows)
    total_fta      = sum(r["fta"]      for r in player_rows)
    total_ftm      = sum(r["ftm"]      for r in player_rows)
    total_oreb     = sum(r["oreb"]     for r in player_rows)
    total_oreb_opp = sum(r["oreb_opp"] for r in player_rows)

    total_all_fga = total_rim_a + total_mid_a + total_fg3a

    # ── Lineup-defensive side ─────────────────────────────────────────────────
    total_opp_rim_fga  = sum(r["opp_rim_fga"]     for r in lineup_rows)
    total_opp_rim_fgm  = sum(r["opp_rim_fgm"]     for r in lineup_rows)
    total_opp_mid_fga  = sum(r["opp_mid_fga"]     for r in lineup_rows)
    total_opp_mid_fgm  = sum(r["opp_mid_fgm"]     for r in lineup_rows)
    total_opp_3p_fga   = sum(r["opp_3p_fga"]      for r in lineup_rows)
    total_opp_3p_fgm   = sum(r["opp_3p_fgm"]      for r in lineup_rows)
    total_forced_to    = sum(r["forced_to"]        for r in lineup_rows)
    total_def_poss     = sum(r["total_def_poss"]   for r in lineup_rows)
    total_dreb         = sum(r["dreb"]             for r in lineup_rows)
    total_dreb_opp     = sum(r["dreb_opp"]         for r in lineup_rows)

    lg = LeagueAverages(
        usage_rate    = _safe_div(total_usage_ev, total_poss),
        rim_fg_pct    = _safe_div(total_rim_m, total_rim_a),
        mid_fg_pct    = _safe_div(total_mid_m, total_mid_a),
        fg3_pct       = _safe_div(total_fg3m, total_fg3a),
        tov_rate      = _safe_div(total_tov, total_usage_ev),
        ft_rate       = _safe_div(total_ft_trips, total_poss),
        ft_pct        = _safe_div(total_ftm, total_fta),
        oreb_rate     = _safe_div(total_oreb, total_oreb_opp),
        shot_rim_rate = _safe_div(total_rim_a, total_all_fga),
        shot_mid_rate = _safe_div(total_mid_a, total_all_fga),
        shot_3p_rate  = _safe_div(total_fg3a,  total_all_fga),
        opp_rim_fg_pct = _safe_div(total_opp_rim_fgm, total_opp_rim_fga),
        opp_mid_fg_pct = _safe_div(total_opp_mid_fgm, total_opp_mid_fga),
        opp_3p_fg_pct  = _safe_div(total_opp_3p_fgm,  total_opp_3p_fga),
        forced_to_rate = _safe_div(total_forced_to, total_def_poss),
        dreb_rate      = _safe_div(total_dreb, total_dreb_opp),
    )
    return lg


def _print_league_averages(lg: LeagueAverages) -> None:
    print(f"  tov_rate    {lg.tov_rate:.4f}  (per usage event; lg_tov for log5)")
    print(f"  rim_fg_pct  {lg.rim_fg_pct:.4f}  |  mid_fg_pct {lg.mid_fg_pct:.4f}  |  fg3_pct {lg.fg3_pct:.4f}")
    print(f"  ft_rate     {lg.ft_rate:.4f}  (per team poss)  |  ft_pct {lg.ft_pct:.4f}")
    print(f"  oreb_rate   {lg.oreb_rate:.4f}")
    print(f"  shot splits rim={lg.shot_rim_rate:.3f}  mid={lg.shot_mid_rate:.3f}  3p={lg.shot_3p_rate:.3f}")
    print(f"  forced_to_rate {lg.forced_to_rate:.4f}  |  dreb_rate {lg.dreb_rate:.4f}")
    print(f"  opp FG%  rim={lg.opp_rim_fg_pct:.4f}  mid={lg.opp_mid_fg_pct:.4f}  3p={lg.opp_3p_fg_pct:.4f}")


# ── Candidate lineup selection ────────────────────────────────────────────────


def get_lineup_players(
    lineup: dict,
    player_lookup: dict[int, dict],
) -> list[dict] | None:
    """Return 5 player_params dicts for a lineup, or None if any player is missing."""
    players = []
    for aid in lineup["lineup"]:
        p = player_lookup.get(int(aid))
        if p is None:
            return None
        players.append(p)
    return players


# ── Test 1: Self-matchup symmetry ────────────────────────────────────────────


def test_self_matchup(
    candidates: list[dict],
    player_lookup: dict[int, dict],
    league: LeagueAverages,
) -> bool:
    print(f"\n{_SEP}")
    print("TEST 1: SELF-MATCHUP SYMMETRY")
    print(f"{_SEP}")
    print("Each lineup simulated against an identical copy of itself.")
    print("Expectation: mean margin = 0 ± 2.0 (CI half-width) by symmetry.\n")

    # Pick up to 3 lineups that have all 5 players in lookup.
    test_lineups = []
    for r in candidates[:10]:
        players = get_lineup_players(r, player_lookup)
        if players is not None:
            test_lineups.append((r, players))
        if len(test_lineups) == 3:
            break

    all_pass = True
    for i, (lu, players) in enumerate(test_lineups):
        key = f"{lu['team_id']}  off_poss={lu['total_off_poss']}  net={lu['net_rating']:+.1f}"
        result = simulate(players, lu, players, lu, league, seed=i + 1)
        ok = abs(result.mean_margin) <= result.ci_half_width + 0.5
        status = "PASS" if ok else "FAIL ***"
        if not ok:
            all_pass = False
        print(f"  [{status}] team={key}")
        print(f"          margin={result.mean_margin:+.2f}  CI±{result.ci_half_width:.2f}  "
              f"n={result.n_sims}  converged={result.converged}")

    print(f"\nTest 1 overall: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


# ── Test 2: Off_rating reproduction ──────────────────────────────────────────


def test_off_rating_reproduction(
    candidates: list[dict],
    player_lookup: dict[int, dict],
    league: LeagueAverages,
) -> bool:
    print(f"\n{_SEP}")
    print("TEST 2: OFF_RATING REPRODUCTION")
    print(f"{_SEP}")
    print("Simulate each lineup vs league-average defense.")
    print("Expectation: simulated pts/100 within ±8 of actual off_rating.\n")
    print(f"  {'team_id':>8}  {'off_poss':>8}  {'actual_off':>10}  {'sim_off':>8}  {'diff':>6}  {'result':>6}")
    print("  " + "-" * 56)

    n_pass = 0
    n_total = 0
    lg_avg_players = make_league_avg_players(league)
    tolerance = 8.0

    for lu in candidates[:7]:
        players = get_lineup_players(lu, player_lookup)
        if players is None:
            continue
        n_total += 1
        # Team B = 5 league-avg players, no lineup history → league-avg defense
        result = simulate(players, lu, lg_avg_players, None, league, seed=99)
        sim_off = result.mean_pts_a / _POSS_PER_SIDE * 100
        actual_off = lu["off_rating"]
        diff = sim_off - actual_off
        ok = abs(diff) <= tolerance
        if ok:
            n_pass += 1
        status = "PASS" if ok else "FAIL"
        print(f"  {lu['team_id']:>8}  {lu['total_off_poss']:>8}  "
              f"{actual_off:>10.1f}  {sim_off:>8.1f}  {diff:>+6.1f}  {status:>6}")

    print(f"\n  {n_pass}/{n_total} lineups within ±{tolerance:.0f} pts/100")
    print(f"\nTest 2 overall: {'PASS' if n_pass >= 5 else 'FAIL (need ≥5/7)'}")
    return n_pass >= 5


# ── Test 3: Monotone ordering ─────────────────────────────────────────────────


def test_monotone_ordering(
    candidates: list[dict],
    player_lookup: dict[int, dict],
    league: LeagueAverages,
) -> bool:
    print(f"\n{_SEP}")
    print("TEST 3: MONOTONE ORDERING")
    print(f"{_SEP}")
    print("When lineup A's real net_rating exceeds lineup B's by ≥10, simulator")
    print("must predict A wins (positive mean margin).  Any inversion = bug.\n")

    # Collect lineups with valid player lookups
    valid = []
    for lu in candidates[:10]:
        players = get_lineup_players(lu, player_lookup)
        if players is not None:
            valid.append((lu, players))

    pairs_tested = 0
    inversions = 0
    net_threshold = 10.0

    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            lu_a, players_a = valid[i]
            lu_b, players_b = valid[j]
            net_diff = lu_a["net_rating"] - lu_b["net_rating"]
            if abs(net_diff) < net_threshold:
                continue
            pairs_tested += 1
            # Ensure A is the better lineup
            if net_diff < 0:
                lu_a, players_a, lu_b, players_b = lu_b, players_b, lu_a, players_a
                net_diff = -net_diff
            result = simulate(players_a, lu_a, players_b, lu_b, league, seed=i * 10 + j)
            correct = result.mean_margin > 0
            if not correct:
                inversions += 1
            status = "PASS" if correct else "INVERSION ***"
            print(f"  [{status}] team {lu_a['team_id']} (net={lu_a['net_rating']:+.1f}) "
                  f"vs team {lu_b['team_id']} (net={lu_b['net_rating']:+.1f})  "
                  f"Δnet={net_diff:+.1f}")
            print(f"          margin={result.mean_margin:+.2f}  n={result.n_sims}")

    if pairs_tested == 0:
        print("  No qualifying pairs found (need |net_rating_diff| ≥ 10).")
        print("  NOTE: With 52 games the top lineups may cluster. Test inconclusive.")
        print("\nTest 3 overall: INCONCLUSIVE (no qualifying pairs)")
        return True  # not a failure

    all_pass = inversions == 0
    print(f"\n  {pairs_tested - inversions}/{pairs_tested} correct orderings  ({inversions} inversions)")
    print(f"\nTest 3 overall: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


# ── Test 4: Distribution sanity ───────────────────────────────────────────────


def test_distribution_sanity(league: LeagueAverages) -> bool:
    print(f"\n{_SEP}")
    print("TEST 4: DISTRIBUTION SANITY  (500 sims, league-avg vs league-avg)")
    print(f"{_SEP}")
    print("Expectation: |mean| < 2,  std ∈ [10, 18],  approximately symmetric.\n")

    lg_players = make_league_avg_players(league)
    # Override _MAX_SIMS for this test: run exactly 500 to inspect the distribution.
    from my5.simulator import (
        LeagueAverages,
        _simulate_game,
        read_lineup_defense,
    )
    import numpy as np

    rng = np.random.default_rng(42)
    defense = read_lineup_defense(None, league)  # league-average defense
    margins: list[float] = []
    for _ in range(500):
        sa, sb = _simulate_game(rng, lg_players, defense, lg_players, defense, league)
        margins.append(float(sa - sb))

    mean_m = statistics.mean(margins)
    std_m  = statistics.stdev(margins)

    # Normality check: skewness and excess kurtosis should be near 0.
    n = len(margins)
    mu = mean_m
    deviations = [x - mu for x in margins]
    m2 = sum(d**2 for d in deviations) / n
    m3 = sum(d**3 for d in deviations) / n
    m4 = sum(d**4 for d in deviations) / n
    skewness = m3 / (m2 ** 1.5) if m2 > 0 else 0
    kurt     = m4 / (m2 ** 2) - 3 if m2 > 0 else 0

    print(f"  n       = {n}")
    print(f"  mean    = {mean_m:+.3f}  (|.| < 2.0 required)")
    print(f"  std dev = {std_m:.3f}  (target 10–18)")
    print(f"  skewness= {skewness:.3f}  (|.| < 0.5 expected for ~Normal)")
    print(f"  kurt    = {kurt:.3f}  (|.| < 1.0 expected for ~Normal)")

    # Percentiles to visualize spread
    sorted_m = sorted(margins)
    p10 = sorted_m[int(0.10 * n)]
    p25 = sorted_m[int(0.25 * n)]
    p75 = sorted_m[int(0.75 * n)]
    p90 = sorted_m[int(0.90 * n)]
    print(f"\n  Percentiles:  10%={p10:+.1f}  25%={p25:+.1f}  75%={p75:+.1f}  90%={p90:+.1f}")

    ok_mean = abs(mean_m) < 2.0
    ok_std  = 10.0 <= std_m <= 18.0
    ok_skew = abs(skewness) < 0.5

    checks = [
        (ok_mean, f"|mean|={abs(mean_m):.2f} < 2.0"),
        (ok_std,  f"std={std_m:.2f} ∈ [10, 18]"),
        (ok_skew, f"|skew|={abs(skewness):.3f} < 0.5"),
    ]
    for ok, msg in checks:
        print(f"\n  [{'PASS' if ok else 'FAIL'}] {msg}")

    all_pass = all(ok for ok, _ in checks)
    print(f"\nTest 4 overall: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print(_SEP)
    print("Phase A Simulator Validation")
    print(_SEP)

    print("\nStep 1: Running aggregation (52 games) ...")
    lineup_metrics, player_params, corrupted = run_aggregation(GAME_IDS)
    lm = lineup_metrics.to_dicts()
    pp = player_params.to_dicts()
    print(f"  Lineup rows: {len(lm)}  |  Player rows: {len(pp)}"
          f"  |  Corrupted stints: {len(corrupted)}")

    print("\nStep 2: Computing league averages ...")
    league = compute_league_averages(lm, pp)
    _print_league_averages(league)

    # Build player lookup: athlete_id (int) → player params dict
    player_lookup = {int(r["athlete_id"]): r for r in pp}

    # Lineups with ≥100 offensive possessions (already sorted descending by off_poss)
    candidates = [r for r in lm if r["total_off_poss"] >= 100]
    print(f"\nStep 3: Lineups with ≥100 off_poss: {len(candidates)}")
    for r in candidates[:7]:
        print(f"  team={r['team_id']:>3}  off_poss={r['total_off_poss']:>4}  "
              f"def_poss={r['total_def_poss']:>4}  "
              f"off={r['off_rating']:>6.1f}  def={r['def_rating']:>6.1f}  "
              f"net={r['net_rating']:>+6.1f}")

    # Run the four validation tests
    r1 = test_self_matchup(candidates, player_lookup, league)
    r2 = test_off_rating_reproduction(candidates, player_lookup, league)
    r3 = test_monotone_ordering(candidates, player_lookup, league)
    r4 = test_distribution_sanity(league)

    # Final summary
    print(f"\n{_SEP}")
    print("VALIDATION SUMMARY")
    print(_SEP)
    results = [
        ("1. Self-matchup symmetry  (most critical)", r1),
        ("2. Off_rating reproduction (±8 pts/100)",  r2),
        ("3. Monotone ordering",                      r3),
        ("4. Distribution sanity",                    r4),
    ]
    overall = all(r for _, r in results)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\nOverall: {'ALL PASS — engine is trustworthy' if overall else 'FAILURES DETECTED — investigate before proceeding'}")
    print(_SEP)


if __name__ == "__main__":
    main()
