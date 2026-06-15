"""
Data-access helpers for play-by-play and roster data.

All ESPN API calls go through here. Cache to data/raw/ as parquet so
re-runs skip the network.

Why infer_schema_length=None on PBP:
  pl.from_dicts() infers column types from only the first N rows (default ~100).
  Several PBP fields are sparse — null in the first hundred rows, then populated
  later by a rare event (three-participant jump balls, coordinator fields on
  half-court heaves, etc.). Without full-scan inference, polars types those
  fields as Null, then crashes when a real value appears. infer_schema_length=None
  forces polars to scan all rows before committing the schema.
"""

from __future__ import annotations

import pathlib

import polars as pl
import sportsdataverse.nba as nba

_RAW_DIR = pathlib.Path("data/raw")


def _raw_dir() -> pathlib.Path:
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    return _RAW_DIR


def load_pbp(game_id: int) -> pl.DataFrame:
    """Return play-by-play for one game, fetching from ESPN and caching to parquet."""
    path = _raw_dir() / f"pbp_{game_id}.parquet"
    if path.exists():
        return pl.read_parquet(path)

    raw = nba.espn_nba_pbp(game_id=game_id)
    plays_dicts = raw.get("plays", [])
    if not plays_dicts:
        raise ValueError(f"No plays returned for game_id={game_id}")

    # infer_schema_length=None: scan ALL rows before committing column types.
    # Required because sparse fields (participants.2, coordinate on rare events,
    # etc.) are null in the first ~100 rows and polars would otherwise type them
    # as Null, crashing when a real value appears later in the file.
    df = pl.from_dicts(plays_dicts, infer_schema_length=None)
    df.write_parquet(path)
    return df


def load_roster(game_id: int) -> pl.DataFrame:
    """Return game roster, fetching from ESPN and caching to parquet."""
    path = _raw_dir() / f"roster_{game_id}.parquet"
    if path.exists():
        return pl.read_parquet(path)
    df = nba.espn_nba_game_rosters(game_id=game_id)
    df.write_parquet(path)
    return df
