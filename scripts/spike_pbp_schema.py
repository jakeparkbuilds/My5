"""
Data-access spike: pull NBA play-by-play for a small slice and inspect its schema.

Strategy:
  1. Fetch the schedule for one date (2024-01-02) to get a handful of game IDs.
  2. Load PBP for the FIRST game only to keep the download tiny.
  3. Cache to data/raw/ as parquet so re-runs skip the network.
  4. Print row count, column dtypes, and 20 sample rows.

Run from repo root:
  source .venv/bin/activate
  python scripts/spike_pbp_schema.py
"""

import pathlib
import polars as pl
from sportsdataverse.nba.nba_schedule import espn_nba_schedule

from my5.loader import load_pbp

RAW_DIR = pathlib.Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

SPIKE_DATE = 20240102   # one regular-season date; gives us ~6-8 games
GAME_INDEX = 0          # we only fetch the first game from that slate


# ── 1. Schedule ──────────────────────────────────────────────────────────────
print(f"\n=== Fetching schedule for {SPIKE_DATE} ===")
schedule = espn_nba_schedule(dates=SPIKE_DATE, season_type=2)
print(f"Games on slate: {len(schedule)}")
print(schedule.select(["game_id", "home_display_name", "away_display_name", "start_date"]).head(10))

game_ids = schedule["game_id"].to_list()
if not game_ids:
    raise RuntimeError("No games found for this date — try a different SPIKE_DATE.")

game_id = int(game_ids[GAME_INDEX])
matchup = f"{schedule['away_display_name'][GAME_INDEX]} @ {schedule['home_display_name'][GAME_INDEX]}"
print(f"\nUsing game_id={game_id}  ({matchup})")


# ── 2. Play-by-play (cached via loader) ──────────────────────────────────────
print(f"\nLoading PBP for game {game_id} …")
plays = load_pbp(game_id)
print(f"Loaded ({len(plays)} rows)")


# ── 3. Schema inspection ─────────────────────────────────────────────────────
print(f"\n=== Row count: {len(plays):,} ===")

print("\n=== Columns and dtypes ===")
for name, dtype in zip(plays.columns, plays.dtypes):
    print(f"  {name:<45} {dtype}")

print(f"\n=== 20 sample rows (all columns, transposed for readability) ===")
sample = plays.head(20)
# Print each row as a dict so long values don't get truncated in the table
for i, row in enumerate(sample.iter_rows(named=True)):
    print(f"\n--- row {i} ---")
    for k, v in row.items():
        if v is not None and v != "" and v != {}:
            print(f"  {k}: {v}")
