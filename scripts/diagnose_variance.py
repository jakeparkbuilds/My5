"""
Variance decomposition for simulator Test 4.

Two runs of 500 league-avg vs league-avg games:
  A) Normal mode: independent Poisson(97) pace per team (current engine)
  B) Fixed mode:  exactly 97 possessions per side (no pace randomness)

Measures:
  - σ(margin) for each run
  - Distribution of per-team game scores (mean, std, min, 5th/95th/max)
  - Variance components

Run with: python scripts/diagnose_variance.py
"""
from __future__ import annotations

import sys
import math
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from my5.simulator import (
    LeagueAverages,
    _simulate_possession,
    log5,
    make_league_avg_players,
    read_lineup_defense,
)

# ── League averages from 52-game aggregation (validate_simulator.py output) ───
LEAGUE = LeagueAverages(
    usage_rate=0.19,
    rim_fg_pct=0.6160,
    mid_fg_pct=0.4389,
    fg3_pct=0.3795,
    tov_rate=0.1119,
    ft_rate=0.0205,
    ft_pct=0.7816,
    oreb_rate=0.0433,
    shot_rim_rate=0.363,
    shot_mid_rate=0.235,
    shot_3p_rate=0.402,
    opp_rim_fg_pct=0.6160,
    opp_mid_fg_pct=0.4389,
    opp_3p_fg_pct=0.3795,
    forced_to_rate=0.1262,
    dreb_rate=0.7214,
)

N_GAMES = 500
POSS = 97
SEED = 42


def simulate_game_poisson(rng, team_a, team_b, defense_a, defense_b):
    """Current engine: independent Poisson(97) per team."""
    poss_a = int(rng.poisson(POSS))
    poss_b = int(rng.poisson(POSS))
    score_a = sum(_simulate_possession(rng, team_a, defense_a, LEAGUE) for _ in range(poss_a))
    score_b = sum(_simulate_possession(rng, team_b, defense_b, LEAGUE) for _ in range(poss_b))
    return score_a, score_b, poss_a, poss_b


def simulate_game_fixed(rng, team_a, team_b, defense_a, defense_b):
    """Fixed pace: exactly 97 possessions per team."""
    score_a = sum(_simulate_possession(rng, team_a, defense_a, LEAGUE) for _ in range(POSS))
    score_b = sum(_simulate_possession(rng, team_b, defense_b, LEAGUE) for _ in range(POSS))
    return score_a, score_b


def stats(arr):
    a = np.array(arr, dtype=float)
    return {
        "mean": a.mean(),
        "std":  a.std(ddof=1),
        "min":  a.min(),
        "p5":   np.percentile(a, 5),
        "p25":  np.percentile(a, 25),
        "p75":  np.percentile(a, 75),
        "p95":  np.percentile(a, 95),
        "max":  a.max(),
    }


def print_stats(label, s, unit="pts"):
    print(f"  {label}:")
    print(f"    mean={s['mean']:.1f} {unit}  std={s['std']:.2f}")
    print(f"    range [{s['min']:.0f}, {s['max']:.0f}]"
          f"   5th={s['p5']:.0f}  25th={s['p25']:.0f}  75th={s['p75']:.0f}  95th={s['p95']:.0f}")


def main():
    players = make_league_avg_players(LEAGUE)
    defense = read_lineup_defense(None, LEAGUE)  # league-average defense for both

    # ── Run A: Poisson pace ───────────────────────────────────────────────────
    print("=" * 60)
    print(f"RUN A — Poisson(97) pace per team  ({N_GAMES} games)")
    print("=" * 60)
    rng = np.random.default_rng(SEED)
    margins_a, scores_a, poss_counts = [], [], []
    for _ in range(N_GAMES):
        sa, sb, pa, pb = simulate_game_poisson(rng, players, players, defense, defense)
        margins_a.append(sa - sb)
        scores_a.extend([sa, sb])
        poss_counts.extend([pa, pb])

    m_a = stats(margins_a)
    s_a = stats(scores_a)
    p_a = stats(poss_counts)
    print_stats("Margin (A - B)", m_a)
    print_stats("Per-team score", s_a)
    print_stats("Possessions per team", p_a, unit="poss")

    # Variance decomposition via law of total variance:
    # Var(score) = E[n]·Var(pts/poss) + Var(n)·E[pts/poss]²
    # We can estimate Var(n) = POSS (Poisson), Var(pts/poss) from fixed-pace run below.

    # ── Run B: Fixed pace ─────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"RUN B — Fixed 97 possessions per team  ({N_GAMES} games)")
    print("=" * 60)
    rng = np.random.default_rng(SEED)
    margins_b, scores_b = [], []
    for _ in range(N_GAMES):
        sa, sb = simulate_game_fixed(rng, players, players, defense, defense)
        margins_b.append(sa - sb)
        scores_b.extend([sa, sb])

    m_b = stats(margins_b)
    s_b = stats(scores_b)
    print_stats("Margin (A - B)", m_b)
    print_stats("Per-team score", s_b)

    # ── Decomposition ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("VARIANCE DECOMPOSITION")
    print("=" * 60)
    var_margin_poisson = m_a["std"] ** 2
    var_margin_fixed   = m_b["std"] ** 2
    var_from_pace      = var_margin_poisson - var_margin_fixed

    var_score_fixed = s_b["std"] ** 2
    e_pts_per_poss  = s_b["mean"] / POSS
    var_score_from_pace = POSS * (e_pts_per_poss ** 2)   # Poisson Var(n) = POSS

    print(f"\n  σ(margin) with Poisson pace : {m_a['std']:.2f} pts")
    print(f"  σ(margin) with fixed pace   : {m_b['std']:.2f} pts")
    print(f"  Variance added by pace      : {var_from_pace:.1f} pt²  "
          f"({100*var_from_pace/var_margin_poisson:.0f}% of total)")
    print()
    print(f"  Per-team σ(score) fixed     : {s_b['std']:.2f} pts")
    print(f"  Mean pts/possession         : {e_pts_per_poss:.4f}")
    print()

    # Analytical prediction for fixed-pace margin σ:
    # Var(margin) = 2 × Var(score) = 2 × (n × Var(pts/poss))
    # Var(pts/poss) = Var(score_fixed) / n
    var_per_poss = var_score_fixed / POSS
    sigma_margin_pred_fixed = math.sqrt(2 * POSS * var_per_poss)
    sigma_margin_pred_poisson = math.sqrt(2 * (POSS * var_per_poss + POSS * e_pts_per_poss**2))
    print(f"  Predicted σ(margin) fixed   : {sigma_margin_pred_fixed:.2f} pts  (vs observed {m_b['std']:.2f})")
    print(f"  Predicted σ(margin) Poisson : {sigma_margin_pred_poisson:.2f} pts  (vs observed {m_a['std']:.2f})")

    # ── NBA reference ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("REAL NBA REFERENCE")
    print("=" * 60)
    print("  NBA game margin σ       : ~11-14 pts  (per-game historical)")
    print("  NBA per-team score      : ~113-117 pts  (recent seasons)")
    print("  NBA per-team score σ    : ~11-13 pts")
    print()
    print(f"  Our simulated mean score : {s_b['mean']:.1f} pts")
    print(f"  Our simulated σ(score)   : {s_b['std']:.2f} pts  (fixed pace)")
    print(f"  Our simulated σ(margin)  : {m_b['std']:.2f} pts  (fixed pace)")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)
    sigma_poisson = m_a["std"]
    sigma_fixed   = m_b["std"]
    pct_pace      = 100 * var_from_pace / var_margin_poisson if var_margin_poisson > 0 else 0
    print(f"""
  Poisson pace σ = {sigma_poisson:.2f}.  Fixed pace σ = {sigma_fixed:.2f}.
  Pace contributes {pct_pace:.0f}% of total variance.

  If σ_fixed ≈ σ_poisson → pace is not the issue, something else inflates variance.
  If σ_fixed << σ_poisson → the independent-Poisson assumption IS the main driver.
""")


if __name__ == "__main__":
    main()
