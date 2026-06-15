"""
Batch reconstruction diagnostic across ~50 games.

For each game: fetch PBP + roster (cache both to data/raw/),
run reconstruct_lineups, record total rows / flagged rows / flag rate.
Also capture any crash or starter-seed failure.

Run from repo root:
  source .venv/bin/activate
  python scripts/batch_reconstruct_diagnostic.py
"""

import logging
import traceback

import polars as pl

from my5.loader import load_pbp, load_roster
from my5.reconstruct import reconstruct_lineups

logging.basicConfig(level=logging.WARNING, format="%(message)s")

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

MATCHUPS = {
    401585087: "CHI@PHI", 401585088: "SAS@MEM", 401585089: "BKN@NOP",
    401585090: "BOS@OKC", 401585091: "ORL@GSW", 401585092: "CHA@SAC",
    401585093: "WAS@CLE", 401585094: "MIL@IND", 401585095: "OKC@ATL",
    401585097: "BKN@HOU", 401585098: "TOR@MEM", 401585099: "NOP@MIN",
    401585096: "CHI@NYK", 401585100: "POR@DAL", 401585101: "LAC@PHX",
    401585102: "DET@UTA", 401585103: "MIA@LAL", 401585104: "ORL@SAC",
    401585107: "UTA@BOS", 401585108: "ATL@IND", 401585109: "OKC@BKN",
    401585110: "WAS@CLE", 401585111: "NYK@PHI", 401585112: "CHA@CHI",
    401585113: "MIN@HOU", 401585114: "LAC@NOP", 401585115: "POR@DAL",
    401585116: "ORL@DEN", 401585117: "MIA@PHX", 401585118: "DET@GSW",
    401585119: "MEM@LAL", 401585120: "TOR@SAC", 401585121: "BOS@IND",
    401585122: "NYK@WAS", 401585123: "UTA@PHI", 401585124: "MIL@HOU",
    401585134: "CHI@CHA", 401585135: "BOS@IND", 401585136: "OKC@WAS",
    401585137: "HOU@MIA", 401585138: "UTA@MIL", 401585139: "PHX@LAC",
    401585145: "MIN@BOS", 401585146: "SAC@CHA", 401585147: "SAS@DET",
    401585148: "WAS@IND", 401585149: "PHI@ATL", 401585150: "OKC@MIA",
    401585151: "HOU@CHI", 401585153: "NOP@GSW", 401585152: "DEN@UTA",
    401585154: "TOR@LAC",
}




results = []
crashes = []

for game_id in GAME_IDS:
    matchup = MATCHUPS.get(game_id, str(game_id))
    print(f"Processing {game_id} ({matchup}) ...", end=" ", flush=True)
    try:
        plays = load_pbp(game_id)
        roster = load_roster(game_id)

        # Capture warnings from reconstruct (invariant violations)
        import io
        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("my5.reconstruct")
        logger.addHandler(handler)

        result = reconstruct_lineups(plays, roster)

        logger.removeHandler(handler)
        log_lines = [l for l in log_capture.getvalue().strip().splitlines() if l]

        total = len(result)
        flagged = total - int(result["lineup_valid"].sum())
        flag_rate = flagged / total if total > 0 else 0.0

        # Collect flagged row details
        flagged_rows = []
        if flagged > 0:
            bad = result.filter(pl.col("lineup_valid") == False)
            for row in bad.select(["game_play_number", "type.text", "text"]).iter_rows(named=True):
                flagged_rows.append(row)

        # Check if any period had OT (period > 4)
        max_period = int(plays["period.number"].max()) if "period.number" in plays.columns else 4

        results.append({
            "game_id": game_id,
            "matchup": matchup,
            "total": total,
            "flagged": flagged,
            "flag_rate": flag_rate,
            "max_period": max_period,
            "flagged_rows": flagged_rows,
            "log_lines": log_lines,
            "crashed": False,
        })
        ot_note = f" [OT: {max_period}P]" if max_period > 4 else ""
        print(f"ok — {total} rows, {flagged} flagged ({flag_rate:.1%}){ot_note}")

    except Exception as e:
        tb = traceback.format_exc()
        crashes.append({"game_id": game_id, "matchup": matchup, "error": str(e), "tb": tb})
        results.append({
            "game_id": game_id, "matchup": matchup, "total": 0, "flagged": 0,
            "flag_rate": 0.0, "max_period": 0, "flagged_rows": [], "log_lines": [],
            "crashed": True, "error": str(e),
        })
        print(f"CRASHED: {e}")


# ── Report ────────────────────────────────────────────────────────────────────
ok_results = [r for r in results if not r["crashed"]]
flag_rates = [r["flag_rate"] for r in ok_results]
flag_rates_sorted = sorted(flag_rates)

import statistics
print("\n" + "="*70)
print("BATCH RECONSTRUCTION DIAGNOSTIC REPORT")
print("="*70)
print(f"\nGames attempted:  {len(results)}")
print(f"Completed OK:     {len(ok_results)}")
print(f"Crashed:          {len(crashes)}")

print(f"\nFlag rate distribution (across {len(ok_results)} completed games):")
print(f"  min:    {min(flag_rates):.3%}")
print(f"  median: {statistics.median(flag_rates):.3%}")
print(f"  max:    {max(flag_rates):.3%}")
clean = sum(1 for r in flag_rates if r == 0.0)
low   = sum(1 for r in flag_rates if 0 < r <= 0.01)
mid   = sum(1 for r in flag_rates if 0.01 < r <= 0.05)
high  = sum(1 for r in flag_rates if r > 0.05)
print(f"  0% (perfectly clean):   {clean}")
print(f"  0–1% (≤1 flag/100):     {low}")
print(f"  1–5%:                   {mid}")
print(f"  >5%:                    {high}")

ot_games = [r for r in ok_results if r["max_period"] > 4]
print(f"\nOvertime games: {len(ot_games)}")
for g in ot_games:
    print(f"  {g['game_id']} {g['matchup']} — {g['max_period']} periods, {g['flagged']} flags")

print("\n" + "-"*70)
print("ALL GAMES — sorted by flag rate descending:")
print("-"*70)
sorted_results = sorted(ok_results, key=lambda r: r["flag_rate"], reverse=True)
print(f"{'game_id':<12} {'matchup':<12} {'total':>6} {'flagged':>8} {'rate':>7} {'OT':>4}")
for r in sorted_results:
    ot = str(r["max_period"]) + "P" if r["max_period"] > 4 else ""
    print(f"{r['game_id']:<12} {r['matchup']:<12} {r['total']:>6} {r['flagged']:>8} {r['flag_rate']:>6.1%} {ot:>4}")

print("\n" + "-"*70)
print("TOP 5 WORST GAMES — flagged rows detail:")
print("-"*70)
for r in sorted_results[:5]:
    if r["flagged"] == 0:
        break
    print(f"\n{r['game_id']} {r['matchup']} — {r['flagged']} flags / {r['total']} rows ({r['flag_rate']:.1%})")
    for fr in r["flagged_rows"]:
        print(f"  gpn={fr['game_play_number']:>4}  {fr['type.text']:<30}  {fr['text'][:60]}")
    print("  Log lines:")
    for line in r["log_lines"]:
        print(f"    {line}")

if crashes:
    print("\n" + "-"*70)
    print("CRASHES:")
    print("-"*70)
    for c in crashes:
        print(f"\n{c['game_id']} {c['matchup']}: {c['error']}")
        print(c["tb"][:600])
