"""
DynamoDB-backed result cache for the simulation engine.

Cache key: sha256( sorted([lineup_key_a, lineup_key_b]).join("|") + "|seed=" + seed )

The sorted pair makes the key symmetric: (A vs B, seed=42) and (B vs A, seed=42)
hit the same entry. The seed IS in the key because the engine is fully deterministic
at a fixed seed — same inputs + same seed → identical bit-for-bit SimResult. A cache
hit is therefore provably equal to a fresh run, not an approximation. Two calls with
the same lineups but different seeds must not share an entry.

If seed is None (non-deterministic run), the cache is bypassed entirely.

Table: my5-sim-cache, PK=cache_key (S). PAY_PER_REQUEST → $0 idle.
TTL=7 days (operational hygiene — avoids unbounded table growth; not a staleness
mitigation, since with seed-in-key there is no staleness).
"""
from __future__ import annotations

import hashlib
import time
from decimal import Decimal
from typing import Any

from my5.config import make_dynamo_resource
from my5.simulator import SimResult

_TABLE_NAME = "my5-sim-cache"
_TTL_SECONDS = 7 * 24 * 3600


def make_cache_key(lineup_key_a: str, lineup_key_b: str, seed: int) -> str:
    """
    Compute the canonical cache key for a matchup.

    Symmetric: key(A, B, s) == key(B, A, s).
    Seed-bound: key(A, B, 42) != key(A, B, 99).
    """
    pair = "|".join(sorted([lineup_key_a, lineup_key_b]))
    return hashlib.sha256(f"{pair}|seed={seed}".encode()).hexdigest()


class SimCache:
    """
    Thin wrapper over the my5-sim-cache DynamoDB table.

    Pass a fake table in tests to avoid network calls:
        cache = SimCache(table=FakeCacheTable())

    Dual-target: same MY5_ENV/USE_LOCAL flag as all other DynamoDB clients.
    """

    def __init__(self, table: Any = None) -> None:
        if table is not None:
            self._table = table
        else:
            self._table = make_dynamo_resource().Table(_TABLE_NAME)

    def get(self, key: str) -> SimResult | None:
        """Return cached SimResult for this key, or None on miss."""
        resp = self._table.get_item(Key={"cache_key": key})
        if "Item" not in resp:
            return None
        item = resp["Item"]
        return SimResult(
            mean_margin=float(item["mean_margin"]),
            ci_half_width=float(item["ci_half_width"]),
            n_sims=int(item["n_sims"]),
            equiv_net_rating=float(item["equiv_net_rating"]),
            converged=bool(item["converged"]),
            mean_pts_a=float(item["mean_pts_a"]),
            mean_pts_b=float(item["mean_pts_b"]),
        )

    def put(self, key: str, result: SimResult) -> None:
        """Store a SimResult. TTL resets on every write (extended on reuse)."""
        self._table.put_item(Item={
            "cache_key":        key,
            "mean_margin":      Decimal(str(result.mean_margin)),
            "ci_half_width":    Decimal(str(result.ci_half_width)),
            "n_sims":           result.n_sims,
            "equiv_net_rating": Decimal(str(result.equiv_net_rating)),
            "converged":        result.converged,
            "mean_pts_a":       Decimal(str(result.mean_pts_a)),
            "mean_pts_b":       Decimal(str(result.mean_pts_b)),
            "ttl":              int(time.time()) + _TTL_SECONDS,
        })
