"""
P3 Load Test: throughput and latency for the caching layer.

Measures the performance difference between cache hits (one DynamoDB read, instant)
and cache misses (full engine run: DynamoDB read + write + SQS + simulation + cache write).

Headline metric: throughput at low p99 latency (jobs/sec across both paths).

Two phases:
  PHASE 1 — WARM + MISS: submit N_MISS unique jobs and run them inline (worker thread
    per job). Each job populates the cache on completion. Latency = end-to-end time.
  PHASE 2 — HIT: submit the same N_HIT jobs again. The cache now has entries, so
    submit_job returns immediately with the cached result. Latency = DynamoDB read only.

Workers run inline (local threads) for cache misses rather than through the real SQS
worker loop. This keeps the load test self-contained and works with both MY5_ENV=local
(DynamoDB Local emulator) and MY5_ENV=aws (real DynamoDB). The throughput measured
is the cache layer's throughput, not the SQS worker pipeline's throughput.

Usage:
    # Local emulators (DynamoDB Local + ElasticMQ must be running):
    MY5_ENV=local python scripts/load_test.py

    # Real AWS (costs ~cents; DynamoDB on-demand):
    MY5_ENV=aws python scripts/load_test.py --n-miss 20 --n-hit 40 --concurrency 8

Options:
    --n-miss      N  Cache-miss jobs (each runs the engine; default 10)
    --n-hit       N  Cache-hit jobs  (each is a DynamoDB read;  default 20)
    --concurrency N  Max parallel threads                        (default 4)
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from my5.cache import SimCache, make_cache_key
from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.queue_client import QueueClient
from my5.simulator import LeagueAverages
from my5.submit_job import _DEFAULT_LEAGUE, submit_job

# ── Test fixtures ─────────────────────────────────────────────────────────────


def _make_player(**overrides: Any) -> dict:
    base = dict(
        usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
        shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
        rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
    )
    base.update(overrides)
    return base


def _make_lineup(lineup_key: str) -> dict:
    return {
        "lineup_key": lineup_key,
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to": 12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55, "dreb_opp": 75, "dreb_rate": 0.733,
    }


class _NoOpQueueClient:
    def delete(self, receipt_handle: str, *, queue_url: str | None = None) -> None:
        pass


# ── Core job execution ────────────────────────────────────────────────────────


def _run_miss_job(
    lineup_key: str,
    seed: int,
    job_store: JobStore,
    sqs_client: QueueClient,
    cache: SimCache,
) -> tuple[float, bool]:
    """
    Submit a cache-miss job, run it inline, return (latency_ms, error).

    Returns (latency_ms, False) on success, (latency_ms, True) on error.
    """
    t0 = time.perf_counter()
    try:
        result = submit_job(
            team_a_key=lineup_key,
            team_a_player_ids=[1, 2, 3, 4, 5],
            team_b_key=lineup_key,
            team_b_player_ids=[6, 7, 8, 9, 10],
            seed=seed,
            league=_DEFAULT_LEAGUE,
            job_store=job_store,
            queue_client=sqs_client,
            cache=cache,
        )
        if result.cache_hit:
            # Already cached from a parallel thread — counts as hit, not error
            return (time.perf_counter() - t0) * 1000, False

        # Run the engine inline — simulates what the SQS worker would do
        players = [_make_player() for _ in range(5)]
        lineup = _make_lineup(lineup_key)
        handle_job(
            result.job_id, "no-receipt",
            queue_client=_NoOpQueueClient(),
            job_store=job_store,
            fetch_lineup=lambda pids, key: (players, lineup),
            cache=cache,
        )
        return (time.perf_counter() - t0) * 1000, False
    except Exception as exc:
        print(f"  [error] miss job failed: {exc!r}", file=sys.stderr)
        return (time.perf_counter() - t0) * 1000, True


def _run_hit_job(
    lineup_key: str,
    seed: int,
    job_store: JobStore,
    sqs_client: QueueClient,
    cache: SimCache,
) -> tuple[float, bool]:
    """Submit a cache-hit job, return (latency_ms, error)."""
    t0 = time.perf_counter()
    try:
        result = submit_job(
            team_a_key=lineup_key,
            team_a_player_ids=[1, 2, 3, 4, 5],
            team_b_key=lineup_key,
            team_b_player_ids=[6, 7, 8, 9, 10],
            seed=seed,
            league=_DEFAULT_LEAGUE,
            job_store=job_store,
            queue_client=sqs_client,
            cache=cache,
        )
        if not result.cache_hit:
            print("  [warn] expected cache hit but got miss", file=sys.stderr)
        return (time.perf_counter() - t0) * 1000, False
    except Exception as exc:
        print(f"  [error] hit job failed: {exc!r}", file=sys.stderr)
        return (time.perf_counter() - t0) * 1000, True


# ── Stats ─────────────────────────────────────────────────────────────────────


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = (p / 100) * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (idx - lo) * (s[hi] - s[lo])


def _dlq_depth() -> int:
    """Read DLQ approximate visible count. Returns 0 if unavailable."""
    try:
        from my5.config import DLQ_URL, USE_LOCAL, make_sqs_client
        if USE_LOCAL:
            return 0
        sqs = make_sqs_client()
        resp = sqs.get_queue_attributes(
            QueueUrl=DLQ_URL,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(resp["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return -1  # -1 means unavailable


# ── Main ──────────────────────────────────────────────────────────────────────


def run_load_test(n_miss: int, n_hit: int, concurrency: int) -> None:
    env = os.getenv("MY5_ENV", "local")
    print("=" * 64)
    print(f"  My5 P3 Load Test  (MY5_ENV={env})")
    print(f"  n_miss={n_miss}  n_hit={n_hit}  concurrency={concurrency}")
    print("=" * 64)

    # Shared DynamoDB clients (thread-safe: boto3 clients are thread-safe)
    job_store = JobStore()
    sqs_client = QueueClient()
    cache = SimCache()

    # Unique lineup keys and seeds for miss jobs
    miss_keys = [f"load_test_{uuid.uuid4().hex[:8]}" for _ in range(n_miss)]
    seed = 42

    # ── PHASE 1: CACHE MISSES ─────────────────────────────────────────────────
    print(f"\n[phase 1] Running {n_miss} cache-miss jobs (concurrency={concurrency})...")
    miss_latencies: list[float] = []
    miss_errors = 0
    t_phase1_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_run_miss_job, key, seed, job_store, sqs_client, cache): key
            for key in miss_keys
        }
        for fut in as_completed(futures):
            latency_ms, error = fut.result()
            miss_latencies.append(latency_ms)
            if error:
                miss_errors += 1
            else:
                print(f"  miss done  latency={latency_ms:.1f}ms")

    t_phase1 = time.perf_counter() - t_phase1_start
    print(f"[phase 1] completed in {t_phase1:.2f}s")

    # ── PHASE 2: CACHE HITS ───────────────────────────────────────────────────
    # Reuse the same lineup_keys that were just populated by phase 1.
    # For n_hit > n_miss, cycle through the miss keys.
    hit_keys = [miss_keys[i % len(miss_keys)] for i in range(n_hit)]

    print(f"\n[phase 2] Running {n_hit} cache-hit jobs (concurrency={concurrency})...")
    hit_latencies: list[float] = []
    hit_errors = 0
    t_phase2_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_run_hit_job, key, seed, job_store, sqs_client, cache): key
            for key in hit_keys
        }
        for fut in as_completed(futures):
            latency_ms, error = fut.result()
            hit_latencies.append(latency_ms)
            if error:
                hit_errors += 1
            else:
                print(f"  hit done   latency={latency_ms:.1f}ms")

    t_phase2 = time.perf_counter() - t_phase2_start
    print(f"[phase 2] completed in {t_phase2:.2f}s")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_jobs = len(miss_latencies) + len(hit_latencies)
    total_time = t_phase1 + t_phase2
    throughput = total_jobs / total_time if total_time > 0 else 0
    hit_rate = len(hit_latencies) / total_jobs * 100 if total_jobs else 0
    dlq = _dlq_depth()

    print()
    print("=" * 64)
    print("  RESULTS")
    print("=" * 64)
    print(f"{'PHASE':<16} {'N':>4}  {'p50 (ms)':>10}  {'p99 (ms)':>10}  {'Errors':>7}")
    print("-" * 64)
    if miss_latencies:
        print(f"{'Cache Miss':<16} {len(miss_latencies):>4}  "
              f"{_percentile(miss_latencies, 50):>10.1f}  "
              f"{_percentile(miss_latencies, 99):>10.1f}  "
              f"{miss_errors:>7}")
    if hit_latencies:
        print(f"{'Cache Hit':<16} {len(hit_latencies):>4}  "
              f"{_percentile(hit_latencies, 50):>10.1f}  "
              f"{_percentile(hit_latencies, 99):>10.1f}  "
              f"{hit_errors:>7}")
    print("-" * 64)
    print()
    print(f"  Throughput (hit+miss combined): {throughput:.1f} jobs/sec")
    print(f"  Cache hit rate:                 {hit_rate:.1f}% ({len(hit_latencies)}/{total_jobs})")
    print(f"  Total errors:                   {miss_errors + hit_errors}")
    if dlq >= 0:
        print(f"  DLQ depth (approx):             {dlq}")
    print("=" * 64)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="My5 P3 caching layer load test")
    parser.add_argument("--n-miss", type=int, default=10, metavar="N",
                        help="Number of cache-miss jobs to run (default: 10)")
    parser.add_argument("--n-hit",  type=int, default=20, metavar="N",
                        help="Number of cache-hit jobs to run (default: 20)")
    parser.add_argument("--concurrency", type=int, default=4, metavar="N",
                        help="Max parallel threads (default: 4)")
    args = parser.parse_args()
    run_load_test(n_miss=args.n_miss, n_hit=args.n_hit, concurrency=args.concurrency)
