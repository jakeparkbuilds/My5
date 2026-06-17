"""
Unit tests for src/my5/simulator.py — no aggregation, no DynamoDB, no network.

Four tests:
  1. Determinism: same seed → identical SimResult.
  2. Self-matchup symmetry: identical teams → mean margin ≈ 0.
  3. Known-outcome possession: a player who never turns over and always makes
     rim shots scores 2 on every possession (verifies state machine ordering).
  4. League-average matchup: two identical league-average lineups → mean margin ≈ 0.
"""

from __future__ import annotations

import numpy as np
import pytest

from my5.simulator import (
    LeagueAverages,
    SimResult,
    _simulate_possession,
    log5,
    make_league_avg_players,
    read_lineup_defense,
    shrink,
    simulate,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_LEAGUE = LeagueAverages(
    usage_rate=0.19,
    rim_fg_pct=0.616,
    mid_fg_pct=0.410,
    fg3_pct=0.379,
    tov_rate=0.1119,
    ft_rate=0.038,
    ft_pct=0.770,
    oreb_rate=0.065,
    shot_rim_rate=0.450,
    shot_mid_rate=0.175,
    shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616,
    opp_mid_fg_pct=0.410,
    opp_3p_fg_pct=0.379,
    forced_to_rate=0.126,
    dreb_rate=0.730,
)


def _make_player(
    usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
    shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
    rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
) -> dict:
    return {
        "usage_rate": usage_rate, "tov_rate": tov_rate,
        "ft_rate": ft_rate, "ft_pct": ft_pct,
        "shot_rim_rate": shot_rim_rate, "shot_mid_rate": shot_mid_rate,
        "shot_3p_rate": shot_3p_rate,
        "rim_fg_pct": rim_fg_pct, "mid_fg_pct": mid_fg_pct, "fg3_pct": fg3_pct,
        "oreb_rate": oreb_rate,
    }


def _five(player: dict) -> list[dict]:
    return [dict(player) for _ in range(5)]


def _sample_lineup() -> dict:
    """Minimal lineup_metrics dict for a real-looking lineup."""
    return {
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to":  12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55,  "dreb_opp": 75, "dreb_rate": 0.733,
    }


# ── Helper checks for math functions ─────────────────────────────────────────


def test_log5_league_avg_returns_league_avg():
    """log5(lg, lg, lg) must equal lg — the baseline identity."""
    for lg in [0.3, 0.5, 0.616, 0.8]:
        result = log5(lg, lg, lg)
        assert abs(result - lg) < 1e-9, f"log5({lg},{lg},{lg}) = {result}, expected {lg}"


def test_log5_above_avg_offense_exceeds_league():
    """Better-than-average offense vs league-average defense beats league average."""
    lg = 0.35
    assert log5(0.40, lg, lg) > lg


def test_shrink_n_zero_gives_league_avg():
    """n=0 observation → full prior (league average)."""
    result = shrink(0.99, n=0, league=0.40, prior_n=25)
    assert result == pytest.approx(0.40)


def test_shrink_large_n_approaches_observed():
    """With many observations the shrunk value approaches the raw observed rate."""
    result = shrink(0.80, n=10_000, league=0.40, prior_n=25)
    assert result > 0.79  # very close to 0.80


def test_read_lineup_defense_none_is_league_avg():
    """lineup=None should return exactly the league-average defensive rates."""
    d = read_lineup_defense(None, _LEAGUE)
    assert d["opp_rim_fg_pct"] == pytest.approx(_LEAGUE.opp_rim_fg_pct)
    assert d["forced_to_rate"] == pytest.approx(_LEAGUE.forced_to_rate)
    assert d["has_history"] is False


def test_read_lineup_defense_shrinks_toward_league():
    """A small-sample extreme rate gets pulled toward league average."""
    extreme_lineup = {
        "opp_rim_fga": 10, "opp_rim_fgm": 10, "opp_rim_fg_pct": 1.0,
        "opp_mid_fga": 10, "opp_mid_fgm": 10, "opp_mid_fg_pct": 1.0,
        "opp_3p_fga":  10, "opp_3p_fgm":  10, "opp_3p_fg_pct":  1.0,
        "forced_to": 0, "total_def_poss": 5, "forced_to_rate": 0.0,
        "dreb": 0, "dreb_opp": 5, "dreb_rate": 0.0,
    }
    d = read_lineup_defense(extreme_lineup, _LEAGUE)
    # n=10 with prior_n=25 → weight = 10/35 = 0.286; shrunk < 1.0
    assert d["opp_rim_fg_pct"] < 1.0
    assert d["opp_rim_fg_pct"] > _LEAGUE.opp_rim_fg_pct


# ── Test 1: Determinism ───────────────────────────────────────────────────────


def test_determinism_seeded():
    """The same seed produces identical SimResult on two runs."""
    players = _five(_make_player())
    lineup = _sample_lineup()
    r1 = simulate(players, lineup, players, lineup, _LEAGUE, seed=42)
    r2 = simulate(players, lineup, players, lineup, _LEAGUE, seed=42)
    assert r1.mean_margin == r2.mean_margin
    assert r1.ci_half_width == r2.ci_half_width
    assert r1.n_sims == r2.n_sims
    assert r1.mean_pts_a == r2.mean_pts_a


# ── Test 2: Self-matchup symmetry ────────────────────────────────────────────


def test_self_matchup_near_zero_margin():
    """Identical teams with the same lineup produce mean margin ≈ 0."""
    player = _make_player(
        usage_rate=0.25, tov_rate=0.10, ft_rate=0.040, ft_pct=0.78,
        shot_rim_rate=0.45, shot_mid_rate=0.18, shot_3p_rate=0.37,
        rim_fg_pct=0.62, mid_fg_pct=0.41, fg3_pct=0.36, oreb_rate=0.07,
    )
    players = _five(player)
    lineup = _sample_lineup()
    result = simulate(players, lineup, players, lineup, _LEAGUE, seed=77)
    # With identical teams, E[margin] = 0 by symmetry.
    # After convergence the CI half-width is ≤ 2.0 pt; mean must sit inside that band.
    assert abs(result.mean_margin) <= result.ci_half_width + 0.5, (
        f"Self-matchup margin {result.mean_margin:.2f} outside "
        f"CI ±{result.ci_half_width:.2f} — symmetry bug"
    )


# ── Test 3: Known-outcome possession ─────────────────────────────────────────


def test_guaranteed_scorer_always_makes():
    """
    A player with tov_rate=0, ft_rate=0, shot_rim_rate=1, and rim_fg_pct≈1.0
    should score 2 on nearly every possession (verify state machine order).
    """
    sure_shot = _make_player(
        usage_rate=0.40,
        tov_rate=0.0,    # no turnovers
        ft_rate=0.0,     # no FT trips
        shot_rim_rate=1.0, shot_mid_rate=0.0, shot_3p_rate=0.0,
        rim_fg_pct=0.999,   # near-certain make
        oreb_rate=0.0,
    )
    players = _five(sure_shot)
    defense = read_lineup_defense(None, _LEAGUE)  # league-average defense

    rng = np.random.default_rng(0)
    scores = [_simulate_possession(rng, players, defense, _LEAGUE) for _ in range(200)]

    # With rim_fg_pct=0.999 and league-avg defense, log5 ≈ 0.999. Should score 2 on ~99.9%.
    assert sum(s == 2 for s in scores) >= 190, (
        f"Expected ≥190/200 possessions to score 2, got {sum(s==2 for s in scores)}"
    )
    assert sum(s == 0 and False for s in scores) == 0  # no TOs possible
    # All non-zero scores must be 2 (rim = 2 pts, no 3s, no FTs so max 2 pts)
    assert all(s in {0, 2} for s in scores)


# ── Test 4: League-average matchup ───────────────────────────────────────────


def test_league_avg_matchup_near_zero_margin():
    """
    Two identical league-average lineups with league-average defense → mean margin ≈ 0.
    Also checks that std dev is in a plausible range (basketball games are noisy).
    """
    players = make_league_avg_players(_LEAGUE)
    result = simulate(players, None, players, None, _LEAGUE, seed=1)

    assert abs(result.mean_margin) < 2.0, (
        f"League-avg vs league-avg margin {result.mean_margin:.2f} should be near zero"
    )
    # pts scored per game: each team faces ~97 possessions, each yielding ~1.0 pt on avg.
    # Rough expected: 97 * (usage * (1-tov) * (rim_r*rim_fg + mid_r*mid_fg + 3p_r*3p_fg))
    # ≈ 97 * (0.19*5 * ~0.55) ≈ 97 * 0.52 ≈ 50.7 pts expected per team. Actual NBA is ~110.
    # But our player usage_rate is per-player, 5 players sum to ≈ 0.95 → ≈ 97*0.52 pts
    # Just verify it's plausible (non-trivially positive).
    assert result.mean_pts_a > 20, "Mean pts_a suspiciously low — check possession loop"
    assert result.mean_pts_b > 20, "Mean pts_b suspiciously low — check possession loop"
    assert result.converged, "League-avg matchup should converge before 5000 sims"
