"""
Monte Carlo possession simulator (P2 Phase A).

Pure engine: takes player-param dicts and lineup-metric dicts (same shapes
as aggregation output) and returns SimResult. No network, no DynamoDB calls.

Possession state machine (per DECISIONS.md 2026-06-16):
  1. Select ball-handler by usage_rate weight.
  2. Turnover check via log5: convert defense's forced_to_rate to per-usage-event
     units (× DEF_POSS_TO_USAGE_EVENT) before blending with player's tov_rate.
  3. FT check: conditional ft_rate (ft_rate / usage_rate). No log5 — no per-lineup
     foul-drawing metric in schema.
  4. Shot type: rim / mid / 3p by shot_*_rate (no defensive shot-type metric).
  5. Make/miss via log5, using shrunk defensive allowed-FG% per zone.
  6. Missed shot → rebound. On OREB: one capped putback (rim shot, log5). No second OREB.

Defensive rates are shrunk at read time via empirical Bayes
  shrunk = (n × observed + prior_n × league) / (n + prior_n)
n=0 (no lineup history) → league average automatically. This handles hypothetical
lineups without any special case in the engine.
(See DECISIONS.md: "Hypothetical lineup defense" and "Defensive lineup rates: shrinkage".)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

# ── Constants ─────────────────────────────────────────────────────────────────

_PRIOR_N_ZONE: int = 25   # shrinkage prior for zone FG% (rim/mid/3p)
_PRIOR_N_RATE: int = 50   # shrinkage prior for rate params (forced_to, dreb)

# Converts lineup's forced_to_rate (per def possession) to per-usage-event
# frame before log5 with player's tov_rate (per usage event).
# Derivation: total_def_poss(10306) / total_usage_events(11628) = 0.8863.
# Update this constant when the full season is aggregated.
# (DECISIONS.md 2026-06-16: lg_tov denominator reconciliation)
DEF_POSS_TO_USAGE_EVENT: float = 0.8863

_CI_TARGET: float = 2.0      # halt when CI half-width ≤ this (points)
_MIN_SIMS: int = 100
_MAX_SIMS: int = 5000
_PROGRESS_INTERVAL: int = 50  # call on_progress every N sims (≤100 writes/job at _MAX_SIMS)
_POSS_PER_SIDE: int = 97     # Poisson mean for pace variation (per team per game)

# ── Public types ──────────────────────────────────────────────────────────────

PlayerParams = dict[str, Any]    # one row from player_params aggregation output
LineupMetrics = dict[str, Any]   # one row from lineup_metrics aggregation output


@dataclass
class LeagueAverages:
    """
    League-wide rate baselines for log5 blending and shrinkage priors.

    Computed from the aggregation output (never hard-coded) and passed into
    the engine. Two families:
      player-side  — from _compute_league_avgs in aggregate.py
      lineup-defense-side — attempt-weighted totals from lineup_metrics
    """
    # Player-side
    usage_rate: float     # usage_events / team_poss (≈ 0.19 per player)
    rim_fg_pct: float     # made / attempted at rim
    mid_fg_pct: float
    fg3_pct: float
    tov_rate: float       # TOV / usage_events (per ball-handling event)
    ft_rate: float        # FT_trips / team_poss (per team possession)
    ft_pct: float         # FTM / FTA
    oreb_rate: float      # player_ORs / team_missed_shot_opportunities
    # Shot-type splits (fraction of FGA by zone, sums to 1)
    shot_rim_rate: float
    shot_mid_rate: float
    shot_3p_rate: float
    # Lineup-defensive side
    opp_rim_fg_pct: float   # allowed rim FGM / FGA across all lineups
    opp_mid_fg_pct: float
    opp_3p_fg_pct: float
    forced_to_rate: float   # forced TOs / def_poss
    dreb_rate: float        # DREB / (opp missed FGA + opp missed final FT)


@dataclass
class SimResult:
    """Output of one full simulation run."""
    mean_margin: float       # mean(team_a_pts − team_b_pts) over n_sims games
    ci_half_width: float     # 1.96 × sqrt(sample_variance / n_sims)
    n_sims: int
    equiv_net_rating: float  # mean_margin / _POSS_PER_SIDE × 100  (pts per 100)
    converged: bool          # False → hit _MAX_SIMS hard cap
    mean_pts_a: float        # for off_rating reproduction validation
    mean_pts_b: float


# ── Math helpers ──────────────────────────────────────────────────────────────


def log5(p_off: float, p_def: float, lg: float) -> float:
    """
    Blend offensive and defensive skill rates against the league baseline.

    p_off : offensive player/lineup's rate for this event
    p_def : defending lineup's allowed rate for this event
    lg    : league-average rate

    When both sides are league-average (p_off=lg and p_def=lg), returns lg.
    Clamped to (0.001, 0.999) to prevent division by zero at extremes.
    """
    p_off = max(0.001, min(0.999, p_off))
    p_def = max(0.001, min(0.999, p_def))
    lg    = max(0.001, min(0.999, lg))
    num   = (p_off * p_def) / lg
    denom = num + (1.0 - p_off) * (1.0 - p_def) / (1.0 - lg)
    return num / denom


def shrink(observed: float, n: int, league: float, prior_n: int) -> float:
    """
    Empirical Bayes: blend observed rate toward the league average.

    n=0  → returns league (no observed data, full prior)
    n>>0 → returns observed (ample data, shrinkage negligible)
    """
    w = n / (n + prior_n)
    return observed * w + league * (1.0 - w)


# ── Defensive rate extraction ─────────────────────────────────────────────────


def read_lineup_defense(
    lineup: LineupMetrics | None,
    lg: LeagueAverages,
) -> dict[str, Any]:
    """
    Return shrunk defensive rates for a lineup.

    lineup=None (hypothetical lineup with no history) is handled identically
    to n=0: the shrinkage formula returns the league average for every metric.
    No special case needed in the engine — see DECISIONS.md "Hypothetical lineup defense."
    """
    if lineup is None:
        return {
            "opp_rim_fg_pct": lg.opp_rim_fg_pct,
            "opp_mid_fg_pct": lg.opp_mid_fg_pct,
            "opp_3p_fg_pct":  lg.opp_3p_fg_pct,
            "forced_to_rate": lg.forced_to_rate,
            "dreb_rate":      lg.dreb_rate,
            "has_history":    False,
        }
    return {
        "opp_rim_fg_pct": shrink(
            float(lineup["opp_rim_fg_pct"]), int(lineup["opp_rim_fga"]),
            lg.opp_rim_fg_pct, _PRIOR_N_ZONE,
        ),
        "opp_mid_fg_pct": shrink(
            float(lineup["opp_mid_fg_pct"]), int(lineup["opp_mid_fga"]),
            lg.opp_mid_fg_pct, _PRIOR_N_ZONE,
        ),
        "opp_3p_fg_pct": shrink(
            float(lineup["opp_3p_fg_pct"]),  int(lineup["opp_3p_fga"]),
            lg.opp_3p_fg_pct, _PRIOR_N_ZONE,
        ),
        "forced_to_rate": shrink(
            float(lineup["forced_to_rate"]), int(lineup["total_def_poss"]),
            lg.forced_to_rate, _PRIOR_N_RATE,
        ),
        "dreb_rate": shrink(
            float(lineup["dreb_rate"]), int(lineup["dreb_opp"]),
            lg.dreb_rate, _PRIOR_N_RATE,
        ),
        "has_history": True,
    }


def make_league_avg_players(league: LeagueAverages) -> list[PlayerParams]:
    """
    Build 5 identical league-average players for use as a neutral opponent.

    Used in off_rating reproduction validation: simulate a real lineup vs
    neutral offense+defense to check simulated pts/100 ≈ historical off_rating.
    """
    p: PlayerParams = {
        "usage_rate":    league.usage_rate,
        "tov_rate":      league.tov_rate,
        "ft_rate":       league.ft_rate,
        "ft_pct":        league.ft_pct,
        "shot_rim_rate": league.shot_rim_rate,
        "shot_mid_rate": league.shot_mid_rate,
        "shot_3p_rate":  league.shot_3p_rate,
        "rim_fg_pct":    league.rim_fg_pct,
        "mid_fg_pct":    league.mid_fg_pct,
        "fg3_pct":       league.fg3_pct,
        "oreb_rate":     league.oreb_rate,
    }
    return [dict(p) for _ in range(5)]


# ── Possession simulation ─────────────────────────────────────────────────────


def _simulate_possession(
    rng: np.random.Generator,
    offense: list[PlayerParams],
    defense: dict[str, Any],
    lg: LeagueAverages,
) -> int:
    """
    Simulate one offensive possession. Returns points scored (0, 1, 2, or 3).

    State machine: select ball-handler → TO → FT → shot type → make/miss → rebound.
    """
    # Step 1: Select ball-handler by usage_rate weight.
    usage = np.array([float(p["usage_rate"]) for p in offense], dtype=float)
    total_usage = usage.sum()
    probs = usage / total_usage if total_usage > 0.0 else np.ones(5) / 5.0
    player = offense[int(rng.choice(5, p=probs))]

    # Step 2: Turnover check via log5.
    # forced_to_rate is per def_poss; multiply by DEF_POSS_TO_USAGE_EVENT to
    # convert to per-usage-event before blending with tov_rate (same denominator).
    p_def_to = float(defense["forced_to_rate"]) * DEF_POSS_TO_USAGE_EVENT
    p_to = log5(float(player["tov_rate"]), p_def_to, lg.tov_rate)
    if rng.random() < p_to:
        return 0

    # Step 3: FT check (no log5; no defensive foul-drawing metric in schema).
    # ft_rate is per team possession; divide by usage_rate to get the conditional
    # probability of a FT trip given this player is handling the ball.
    usage_rate = max(float(player["usage_rate"]), 0.001)
    p_ft = min(float(player["ft_rate"]) / usage_rate, 1.0)
    if rng.random() < p_ft:
        # Standard 2-shot trip (canonical NBA shooting-foul trip length).
        return (int(rng.random() < float(player["ft_pct"])) +
                int(rng.random() < float(player["ft_pct"])))

    # Step 4: Shot type selection (offense tendency only; no defensive adjustment).
    rim_r = float(player["shot_rim_rate"])
    mid_r = float(player["shot_mid_rate"])
    fg3_r = float(player["shot_3p_rate"])
    total = rim_r + mid_r + fg3_r
    if total <= 0.0:
        # Player with no FGA history — fall back to league-average shot split.
        rim_r, mid_r, fg3_r = lg.shot_rim_rate, lg.shot_mid_rate, lg.shot_3p_rate
        total = rim_r + mid_r + fg3_r

    roll = rng.random() * total
    if roll < rim_r:
        zone = "rim"
    elif roll < rim_r + mid_r:
        zone = "mid"
    else:
        zone = "three"

    # Step 5: Make/miss via log5 (per-zone offensive rate × defensive allowed rate).
    if zone == "rim":
        p_make = log5(float(player["rim_fg_pct"]), float(defense["opp_rim_fg_pct"]), lg.rim_fg_pct)
        pts_made = 2
    elif zone == "mid":
        p_make = log5(float(player["mid_fg_pct"]), float(defense["opp_mid_fg_pct"]), lg.mid_fg_pct)
        pts_made = 2
    else:
        p_make = log5(float(player["fg3_pct"]), float(defense["opp_3p_fg_pct"]), lg.fg3_pct)
        pts_made = 3

    if rng.random() < p_make:
        return pts_made

    # Step 6: Missed shot → rebound.
    # P(OREB) = 1 − dreb_rate (defense fails to secure the defensive rebound).
    if rng.random() > float(defense["dreb_rate"]):
        # OREB: one capped putback attempt, selected by oreb_rate weight.
        oreb_w = np.array([float(p["oreb_rate"]) for p in offense], dtype=float)
        oreb_total = oreb_w.sum()
        if oreb_total > 0.0:
            putback_player = offense[int(rng.choice(5, p=oreb_w / oreb_total))]
        else:
            putback_player = player
        # Putback is always a rim shot (physical proximity to basket).
        p_putback = log5(
            float(putback_player["rim_fg_pct"]),
            float(defense["opp_rim_fg_pct"]),
            lg.rim_fg_pct,
        )
        if rng.random() < p_putback:
            return 2
        # No second OREB — possession ends.

    return 0


def _simulate_game(
    rng: np.random.Generator,
    team_a: list[PlayerParams],
    defense_vs_a: dict[str, Any],
    team_b: list[PlayerParams],
    defense_vs_b: dict[str, Any],
    lg: LeagueAverages,
) -> tuple[int, int]:
    """
    Simulate one game. Returns (score_a, score_b).

    Pace is coupled: one Poisson(2 × _POSS_PER_SIDE) draw sets total game tempo,
    then split evenly between the two teams. Both teams play in the same game, so
    their possession counts cannot diverge independently — independent draws
    would allow impossible splits (e.g. 80 vs 114) and inflate margin variance by
    ~43% relative to coupled pace. (See DECISIONS.md 2026-06-17.)
    """
    total_poss = int(rng.poisson(_POSS_PER_SIDE * 2))
    poss_a = total_poss // 2
    poss_b = total_poss - poss_a
    score_a = sum(_simulate_possession(rng, team_a, defense_vs_a, lg) for _ in range(poss_a))
    score_b = sum(_simulate_possession(rng, team_b, defense_vs_b, lg) for _ in range(poss_b))
    return score_a, score_b


# ── Public API ────────────────────────────────────────────────────────────────


def simulate(
    team_a_players: list[PlayerParams],
    team_a_lineup: LineupMetrics | None,
    team_b_players: list[PlayerParams],
    team_b_lineup: LineupMetrics | None,
    league: LeagueAverages,
    seed: int | None = None,
    on_progress: Callable[[int, float], None] | None = None,
) -> SimResult:
    """
    Run Monte Carlo simulation of team_a vs team_b to CI convergence.

    Parameters
    ----------
    team_a_players : 5 player_params dicts (aggregation output shape)
    team_a_lineup  : lineup_metrics dict for team A (None = hypothetical)
    team_b_players : 5 player_params dicts
    team_b_lineup  : lineup_metrics dict for team B (None = hypothetical)
    league         : league-wide baselines (see LeagueAverages)
    seed           : RNG seed for deterministic replay; None uses system entropy
    on_progress    : optional callback(sims_done, ci_half_width) fired every
                     _PROGRESS_INTERVAL sims. When None: behavior is byte-for-byte
                     identical to before — same seed → same SimResult. The callback
                     is a pure side-effect; it does not touch the RNG or Welford state.

    Stopping rule: Welford's online variance, stop when
    1.96 × sqrt(var/n) ≤ _CI_TARGET (2.0 pts) AND n ≥ _MIN_SIMS (100).
    Hard cap at _MAX_SIMS (5000).

    Hypothetical lineups (lineup=None) automatically receive league-average
    defense — no special case in the caller required.
    """
    rng = np.random.default_rng(seed)

    # team B's defense faces team A's offense; team A's defense faces team B's.
    defense_vs_a = read_lineup_defense(team_b_lineup, league)
    defense_vs_b = read_lineup_defense(team_a_lineup, league)

    # Welford's online mean / variance (numerically stable, no list of margins needed).
    n = 0
    M = 0.0     # running mean of (score_a − score_b)
    S = 0.0     # running sum of squared deviations for variance
    sum_a = 0.0
    sum_b = 0.0

    while n < _MAX_SIMS:
        score_a, score_b = _simulate_game(
            rng, team_a_players, defense_vs_a, team_b_players, defense_vs_b, league
        )
        margin = float(score_a - score_b)

        n += 1
        M_prev = M
        M += (margin - M) / n
        S += (margin - M_prev) * (margin - M)
        sum_a += score_a
        sum_b += score_b

        if n >= 2:
            var = S / (n - 1)
            ci_now = 1.96 * math.sqrt(var / n)
            if on_progress is not None and n % _PROGRESS_INTERVAL == 0:
                on_progress(n, round(ci_now, 2))
            if n >= _MIN_SIMS and ci_now <= _CI_TARGET:
                break

    var = S / max(n - 1, 1)
    ci_half = 1.96 * math.sqrt(var / n) if n > 1 else float("inf")

    return SimResult(
        mean_margin=round(M, 2),
        ci_half_width=round(ci_half, 2),
        n_sims=n,
        equiv_net_rating=round(M / _POSS_PER_SIDE * 100, 1),
        converged=ci_half <= _CI_TARGET,
        mean_pts_a=round(sum_a / n, 2),
        mean_pts_b=round(sum_b / n, 2),
    )
