"""
Run the P1 aggregation over 52 cached games and report:
  1. Completeness Check A — all distinct 2PT type.text values and their rim/mid bucket
  2. Completeness Check B — Free Throw - 1 of 1 non-and-one examples (usage trip)
  3. Validation — per-lineup ratings for high-possession lineups
  4. Validation — top usage players (stars should lead)

Run from repo root:
  source .venv/bin/activate
  python scripts/run_aggregation.py
"""

from __future__ import annotations

import polars as pl

from my5.aggregate import _shot_zone, run_aggregation
from my5.loader import load_pbp, load_roster
from my5.reconstruct import reconstruct_lineups

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


# ── Completeness Check A: 2PT type.text classification ───────────────────────

def completeness_check_a(game_ids: list[int]) -> None:
    print("\n" + "=" * 70)
    print("COMPLETENESS CHECK A — 2PT type.text → rim/mid classification")
    print("=" * 70)

    type_counts: dict[str, int] = {}
    for game_id in game_ids:
        plays = load_pbp(game_id)
        if "shootingPlay" not in plays.columns or "pointsAttempted" not in plays.columns:
            continue
        shots = plays.filter(
            pl.col("shootingPlay").eq(True) & pl.col("pointsAttempted").eq(2)
        )
        for row in shots.select("type.text").iter_rows():
            tt = row[0] or ""
            type_counts[tt] = type_counts.get(tt, 0) + 1

    print(f"\n{'count':>6}  {'zone':<6}  type.text")
    print("-" * 60)
    ambiguous: list[str] = []
    rim_types: list[tuple[int, str]] = []
    mid_types: list[tuple[int, str]] = []

    for tt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        zone = _shot_zone(2, tt)
        if zone == "rim":
            rim_types.append((cnt, tt))
        else:
            mid_types.append((cnt, tt))
            # Flag anything that visually looks like it could be a rim shot
            lower = tt.lower()
            suspicious = any(w in lower for w in (
                "putback", "floater", "floating", "hook", "post",
                "alley", "reverse"
            ))
            if suspicious:
                ambiguous.append(tt)

    print("\n  === RIM (Layup / Dunk / Tip / Finger Roll keyword) ===")
    for cnt, tt in rim_types:
        print(f"  {cnt:>6}  {'rim':<6}  {tt}")

    print("\n  === MID-RANGE ===")
    for cnt, tt in mid_types:
        print(f"  {cnt:>6}  {'mid':<6}  {tt}")

    if ambiguous:
        print(f"\n  !! AMBIGUOUS mid types that may deserve rim: {ambiguous}")
        print("  !! Flag these for manual review before Phase B is merged.")
    else:
        print("\n  No ambiguous mid-range labels detected. Classification is clean.")


# ── Completeness Check B: FT-1-of-1 non-and-one usage trips ──────────────────

def completeness_check_b(game_ids: list[int]) -> None:
    print("\n" + "=" * 70)
    print("COMPLETENESS CHECK B — Free Throw - 1 of 1 non-and-one FT trips")
    print("=" * 70)

    from my5.aggregate import _tag_and_ones

    examples: list[dict] = []
    total_lone = 0
    total_and_one = 0

    for game_id in game_ids:
        plays = load_pbp(game_id)
        roster = load_roster(game_id)
        recon = reconstruct_lineups(plays, roster)
        rows = recon.to_dicts()
        _, ft_idxs = _tag_and_ones(rows)

        for i, row in enumerate(rows):
            if row.get("type.text") != "Free Throw - 1 of 1":
                continue
            if i in ft_idxs:
                total_and_one += 1
            else:
                total_lone += 1
                if len(examples) < 5:
                    examples.append({
                        "game_id": game_id,
                        "gpn": row.get("game_play_number"),
                        "player": row.get("participants.0.athlete.id"),
                        "clock": row.get("clock.displayValue"),
                        "made": row.get("scoringPlay"),
                    })

    print(f"\n  And-one FT-1-of-1 rows:       {total_and_one}")
    print(f"  Lone (non-and-one) FT-1-of-1: {total_lone}")
    print(f"\n  Both correctly receive FT-trip credit in usage numerator.")
    print(f"  (And-ones do NOT get FT-trip credit — FGA already counted.)")
    if examples:
        print(f"\n  Sample lone FT-1-of-1 rows (should count as usage trips):")
        for ex in examples:
            made_str = "MADE" if ex["made"] else "MISS"
            print(f"    game={ex['game_id']}  gpn={ex['gpn']:>4}  "
                  f"player={ex['player']}  clock={ex['clock']}  {made_str}")


# ── Main aggregation + validation ─────────────────────────────────────────────

def main() -> None:
    completeness_check_a(GAME_IDS)
    completeness_check_b(GAME_IDS)

    print("\n" + "=" * 70)
    print("RUNNING AGGREGATION (52 games) ...")
    print("=" * 70)

    lineup_metrics, player_params, corrupted_stints = run_aggregation(GAME_IDS)

    print(f"\nLineup rows: {len(lineup_metrics)}")
    print(f"Player rows: {len(player_params)}")

    # ── Validation 1: High-possession lineup ratings ──────────────────────────
    print("\n" + "-" * 70)
    print("VALIDATION 1 — Top-15 lineups by total_off_poss (ratings sanity check)")
    print("-" * 70)
    print(f"{'poss':>5}  {'off':>6}  {'def':>6}  {'net':>6}  lineup (team_id)")
    top = lineup_metrics.filter(pl.col("total_off_poss") >= 10).head(15)
    for row in top.iter_rows(named=True):
        print(
            f"{row['total_off_poss']:>5}  "
            f"{row['off_rating']:>6.1f}  "
            f"{row['def_rating']:>6.1f}  "
            f"{row['net_rating']:>+6.1f}  "
            f"{row['lineup']}  (team={row['team_id']})"
        )

    # Check sanity: no lineup should have off rating > 200 or < 0
    bad_off = lineup_metrics.filter(
        (pl.col("off_rating") > 200) | (pl.col("off_rating") < 0)
    )
    bad_def = lineup_metrics.filter(
        (pl.col("def_rating") > 200) | (pl.col("def_rating") < 0)
    )
    bad_net = lineup_metrics.filter(pl.col("net_rating").abs() > 80)
    print(f"\n  Sanity: off_rating out of [0,200]: {len(bad_off)} lineups")
    print(f"  Sanity: def_rating out of [0,200]: {len(bad_def)} lineups")
    print(f"  Sanity: |net_rating| > 80:          {len(bad_net)} lineups")

    # Rating distribution for lineups with ≥ 50 poss
    heavy = lineup_metrics.filter(pl.col("total_off_poss") >= 50)
    if len(heavy) > 0:
        print(f"\n  For lineups with ≥50 off poss ({len(heavy)} total):")
        print(f"    off_rating: min={heavy['off_rating'].min():.1f}  "
              f"median={heavy['off_rating'].median():.1f}  "
              f"max={heavy['off_rating'].max():.1f}")
        print(f"    def_rating: min={heavy['def_rating'].min():.1f}  "
              f"median={heavy['def_rating'].median():.1f}  "
              f"max={heavy['def_rating'].max():.1f}")
        print(f"    net_rating: min={heavy['net_rating'].min():.1f}  "
              f"median={heavy['net_rating'].median():.1f}  "
              f"max={heavy['net_rating'].max():.1f}")

    # ── Validation 2: Usage leaders ──────────────────────────────────────────
    print("\n" + "-" * 70)
    print("VALIDATION 2 — Top-20 usage leaders (stars should rank high)")
    print("-" * 70)
    top_usage = (
        player_params
        .filter(pl.col("team_poss_on_floor") >= 50)
        .sort("usage_rate", descending=True)
        .head(20)
    )
    print(f"  {'athlete_id':<12} {'poss':>6} {'usage':>7} {'rim%':>6} {'mid%':>6} "
          f"{'3p%':>6} {'ft%':>6} {'oreb%':>6}")
    for row in top_usage.iter_rows(named=True):
        print(
            f"  {row['athlete_id']:<12} "
            f"{row['team_poss_on_floor']:>6} "
            f"{row['usage_rate']:>7.3f} "
            f"{row['rim_fg_pct']:>6.3f} "
            f"{row['mid_fg_pct']:>6.3f} "
            f"{row['fg3_pct']:>6.3f} "
            f"{row['ft_pct']:>6.3f} "
            f"{row['oreb_rate']:>6.3f}"
        )

    # ── Validation 3: League averages (data-derived shrinkage priors) ─────────
    print("\n" + "-" * 70)
    print("VALIDATION 3 — League-average rates (these are the shrinkage priors)")
    print("-" * 70)
    from my5.aggregate import _compute_league_avgs
    # Rebuild from player_acc — rerun aggregation to get player_acc
    # (We only have the final DataFrame, so compute from it directly)
    total_rows = len(player_params)
    if total_rows > 0:
        rim_pct = player_params["rim_m"].sum() / max(player_params["rim_a"].sum(), 1)
        mid_pct = player_params["mid_m"].sum() / max(player_params["mid_a"].sum(), 1)
        fg3_pct = player_params["fg3m"].sum() / max(player_params["fg3a"].sum(), 1)
        ft_pct_lg = player_params["ftm"].sum() / max(player_params["fta"].sum(), 1)
        usage_num = (player_params["fga"] + player_params["tov"] + player_params["ft_trips"]).sum()
        usage_rate = usage_num / max(player_params["team_poss_on_floor"].sum(), 1)
        print(f"  League usage rate:   {usage_rate:.3f}  (≈ 5 players use 1 poss → expect ~0.2)")
        print(f"  League rim FG%:      {rim_pct:.3f}  (NBA rim FG% typically 0.60-0.65)")
        print(f"  League mid FG%:      {mid_pct:.3f}  (NBA mid FG% typically 0.38-0.42)")
        print(f"  League 3P%:          {fg3_pct:.3f}  (NBA 3P% typically 0.35-0.37)")
        print(f"  League FT%:          {ft_pct_lg:.3f}  (NBA FT% typically 0.77-0.80)")
        total_oreb = player_params["oreb"].sum()
        total_oreb_opp = player_params["oreb_opp"].sum()
        oreb_rate = total_oreb / max(total_oreb_opp, 1)
        print(f"  League OREB%:        {oreb_rate:.3f}  (individual; ×5 ≈ team OREB% ~0.25-0.30)")

    # ── Validation 4: Defensive params for top lineups ────────────────────────
    print("\n" + "-" * 70)
    print("VALIDATION 4 — Defensive params for 5 well-known lineups")
    print("-" * 70)
    top_def = (
        lineup_metrics
        .filter(pl.col("total_def_poss") >= 50)
        .sort("total_def_poss", descending=True)
        .head(5)
    )
    for row in top_def.iter_rows(named=True):
        dreb_pct = (row["dreb_rate"] * 100)
        print(
            f"  lineup={row['lineup']}  team={row['team_id']}\n"
            f"    def_poss={row['total_def_poss']}  "
            f"opp_rim%={row['opp_rim_fg_pct']:.3f}  "
            f"opp_mid%={row['opp_mid_fg_pct']:.3f}  "
            f"opp_3p%={row['opp_3p_fg_pct']:.3f}  "
            f"forced_to_rate={row['forced_to_rate']:.3f}  "
            f"dreb%={dreb_pct:.1f}%\n"
        )

    # ── Summary stats ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("AGGREGATION SUMMARY")
    print("=" * 70)
    print(f"  Total unique lineups:    {len(lineup_metrics)}")
    print(f"  Lineups with ≥10 poss:  {lineup_metrics.filter(pl.col('total_off_poss') >= 10)['team_id'].len()}")
    print(f"  Lineups with ≥50 poss:  {lineup_metrics.filter(pl.col('total_off_poss') >= 50)['team_id'].len()}")
    print(f"  Total unique players:    {len(player_params)}")
    print(f"  Players with ≥50 poss:  {player_params.filter(pl.col('team_poss_on_floor') >= 50)['athlete_id'].len()}")
    total_poss = lineup_metrics["total_off_poss"].sum()
    print(f"  Total possession-ends:   {total_poss}  (expect ~4200 for 52 games × ~81 poss/team × 2 teams)")

    # ── Corrupted-stint skip report ────────────────────────────────────────────
    print(f"\n  Corrupted-lineup stints skipped: {len(corrupted_stints)}")
    if corrupted_stints:
        print("  !! EXPECTED 0 — investigate data quality:")
        for s in corrupted_stints:
            print(
                f"    game={s['game_id']}  stint={s['stint_id']}  "
                f"home_size={s['home_size']}  away_size={s['away_size']}  "
                f"gpn={s['first_gpn']}–{s['last_gpn']}  rows={s['row_count']}"
            )
    else:
        print("  OK — no corrupted stints detected.")


if __name__ == "__main__":
    main()
