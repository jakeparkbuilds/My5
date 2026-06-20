"""
End-to-end proof for P2-C-AWS: WebSocket live progress through real AWS.

Architecture under test
-----------------------
  1. Job record → real my5-sim-jobs (DynamoDB us-east-1)
  2. WS client  → live wss:// (APIGW WebSocket)
  3.              connect_handler Lambda registers conn in my5-ws-connections
  4. Worker     → runs locally, MY5_ENV=aws, writes to real DynamoDB
  5.              each update_item → DynamoDB Streams → fanout_handler Lambda
  6.                              → post_to_connection → APIGW → client frame
  7. Assert: climbing progress, exactly one "done", mean_margin == direct simulate()

Note on SQS: this script bypasses SQS and calls handle_job directly so we can
control timing (client must be connected before the worker starts writing). The
real SQS path is verified by the queue e2e; here we isolate the WebSocket path.

Usage:
    # Grab the wss URL from terraform output:
    WSS=$(terraform -chdir=infra output -raw ws_url)
    MY5_ENV=aws python scripts/e2e_ws_aws.py "$WSS"

    # Or pass it directly:
    MY5_ENV=aws python scripts/e2e_ws_aws.py wss://abc123.execute-api.us-east-1.amazonaws.com/prod
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import websockets

from my5.job_store import JobStore
from my5.job_worker import handle_job
from my5.simulator import LeagueAverages, simulate

# ── Config ────────────────────────────────────────────────────────────────────

_SEED = 42

_LEAGUE = LeagueAverages(
    usage_rate=0.19, rim_fg_pct=0.616, mid_fg_pct=0.410, fg3_pct=0.379,
    tov_rate=0.1119, ft_rate=0.038, ft_pct=0.770, oreb_rate=0.065,
    shot_rim_rate=0.450, shot_mid_rate=0.175, shot_3p_rate=0.375,
    opp_rim_fg_pct=0.616, opp_mid_fg_pct=0.410, opp_3p_fg_pct=0.379,
    forced_to_rate=0.126, dreb_rate=0.730,
)


# ── Fake helpers (no real player data required in DynamoDB) ───────────────────


def _make_player(**overrides: Any) -> dict:
    base = dict(
        usage_rate=0.20, tov_rate=0.10, ft_rate=0.038, ft_pct=0.77,
        shot_rim_rate=0.45, shot_mid_rate=0.175, shot_3p_rate=0.375,
        rim_fg_pct=0.616, mid_fg_pct=0.41, fg3_pct=0.379, oreb_rate=0.065,
    )
    base.update(overrides)
    return base


def _make_lineup() -> dict:
    return {
        "lineup_key": "e2e_key",
        "opp_rim_fga": 80, "opp_rim_fgm": 49, "opp_rim_fg_pct": 0.612,
        "opp_mid_fga": 40, "opp_mid_fgm": 16, "opp_mid_fg_pct": 0.400,
        "opp_3p_fga":  60, "opp_3p_fgm":  21, "opp_3p_fg_pct":  0.350,
        "forced_to": 12, "total_def_poss": 90, "forced_to_rate": 0.133,
        "dreb": 55, "dreb_opp": 75, "dreb_rate": 0.733,
    }


class _NoOpQueueClient:
    """Stub queue client — handle_job calls delete(); we handle messaging ourselves."""
    def delete(self, receipt_handle: str, *, queue_url: str | None = None) -> None:
        pass


def _make_job_record(job_id: str, seed: int) -> dict:
    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "job_id":            job_id,
        "status":            "queued",
        "attempt_count":     0,
        "team_a_key":        "hypothetical",
        "team_b_key":        "hypothetical",
        "team_a_player_ids": [1, 2, 3, 4, 5],
        "team_b_player_ids": [6, 7, 8, 9, 10],
        "league":            {k: Decimal(str(v)) for k, v in dataclasses.asdict(_LEAGUE).items()},
        "seed":              seed,
        "queued_at":         now,
        "ttl":               int(time.time()) + 86400,
    }


# ── Main e2e flow ─────────────────────────────────────────────────────────────


async def main(wss_url: str) -> None:
    print("=" * 64)
    print("  My5 P2-C-AWS End-to-End WebSocket Proof (LIVE AWS)")
    print("  Account: real us-east-1  |  MY5_ENV=aws required")
    print("=" * 64)

    # ── 1. Create job record on REAL DynamoDB ─────────────────────────────────
    job_store = JobStore()   # MY5_ENV=aws → real my5-sim-jobs
    job_id = str(uuid.uuid4())
    print(f"\n[setup] Writing job {job_id[:8]}... to real my5-sim-jobs")
    job_store.put_job(_make_job_record(job_id, _SEED))
    print("[setup] Job written ✓")

    # ── 2. Connect WebSocket to live APIGW ───────────────────────────────────
    #   connect_handler Lambda runs synchronously during the WS handshake.
    #   By the time connect() returns, the conn is registered in my5-ws-connections.
    ws_endpoint = f"{wss_url}?job_id={job_id}"
    print(f"\n[client] Connecting to {wss_url}?job_id={job_id[:8]}...")
    frames: list[dict] = []
    worker_done = threading.Event()

    async with websockets.connect(ws_endpoint, open_timeout=15) as ws:
        print("[client] Connected ✓  (connect_handler Lambda registered conn)")

        # ── 3. Start worker AFTER connection is established ───────────────────
        players = [_make_player() for _ in range(5)]
        lineup = _make_lineup()

        def _run_worker() -> None:
            try:
                handle_job(
                    job_id, "no-receipt",
                    queue_client=_NoOpQueueClient(),
                    job_store=job_store,                        # real DynamoDB
                    fetch_lineup=lambda pids, key: (players, lineup),
                )
            finally:
                worker_done.set()

        worker_thread = threading.Thread(target=_run_worker, daemon=True)
        worker_thread.start()
        print("[worker] Started (local worker → real DynamoDB → Streams → fanout Lambda)")

        # ── 4. Collect frames ─────────────────────────────────────────────────
        # AWS path: no snapshot frame — first frame is the first progress update.
        # fanout_handler Lambda latency is ~100-500ms per frame (Lambda cold start
        # on first invocation may add 2-3s; warm invocations are fast).
        print("[client] Waiting for frames from APIGW WebSocket...\n")
        try:
            async with asyncio.timeout(120):
                async for raw in ws:
                    frame = json.loads(raw)
                    frames.append(frame)

                    if frame["type"] == "progress" and frame["sims_done"] > 0:
                        print(f"  progress: {frame['sims_done']:>5} sims  CI ±{frame['ci_half']:.2f} pts")
                    elif frame["type"] == "progress":
                        print("  (snapshot: sims_done=0)")

                    if frame["type"] in ("done", "failed"):
                        break
        except TimeoutError:
            raise RuntimeError("Timed out waiting for terminal frame after 120s")

    worker_thread.join(timeout=30.0)

    # ── 5. Assertions ─────────────────────────────────────────────────────────
    print()
    assert frames, "No frames received — check Lambda logs for connect_handler/fanout_handler"

    terminal = frames[-1]
    assert terminal["type"] == "done", (
        f"Expected 'done' terminal frame, got: {terminal}"
    )

    progress_frames = [f for f in frames if f["type"] == "progress" and f["sims_done"] > 0]
    done_frames     = [f for f in frames if f["type"] == "done"]

    assert len(done_frames) == 1, f"Expected exactly 1 done frame, got {len(done_frames)}"
    assert len(progress_frames) >= 1, "Expected at least 1 progress frame with sims_done > 0"

    sims_sequence = [f["sims_done"] for f in progress_frames]
    assert sims_sequence == sorted(sims_sequence), (
        f"progress sims_done not monotone: {sims_sequence}"
    )

    print(f"  DONE after {terminal['n_sims']} sims")
    print(f"    mean_margin      = {terminal['mean_margin']:+.3f} pts")
    print(f"    ci_half_width    = {terminal['ci_half_width']:.3f} pts")
    print(f"    equiv_net_rating = {terminal['equiv_net_rating']:+.1f} pts/100")
    print(f"    converged        = {terminal['converged']}")

    # ── 6. Direct-vs-stack invariant ─────────────────────────────────────────
    direct = simulate(players, lineup, players, lineup, _LEAGUE, seed=_SEED)
    delta = abs(terminal["mean_margin"] - direct.mean_margin)
    assert delta < 1e-6, (
        f"Stack margin {terminal['mean_margin']:.6f} ≠ direct {direct.mean_margin:.6f}"
    )
    print(f"\n  Direct simulate(seed={_SEED}) margin = {direct.mean_margin:+.3f} pts")
    print(f"  Stack vs direct delta              = {delta:.2e}  ✓")

    # ── 7. Frame sequence summary ─────────────────────────────────────────────
    print(f"\n  Total frames received : {len(frames)}")
    print(f"  Progress frames       : {len(progress_frames)}")
    print(f"  Terminal frames       : {len(done_frames)}")
    print(f"  sims_done sequence    : {sims_sequence}")

    print("\n" + "=" * 64)
    print("  P2-C-AWS E2E PASSED ✓  (real AWS WebSocket, real Lambda fanout)")
    print("=" * 64)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Try to get URL from terraform output
        import subprocess
        result = subprocess.run(
            ["terraform", "-chdir=infra", "output", "-raw", "ws_url"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip().startswith("wss://"):
            wss = result.stdout.strip()
            print(f"[auto] Using wss URL from terraform output: {wss}")
        else:
            print("Usage: MY5_ENV=aws python scripts/e2e_ws_aws.py <wss://...>")
            print("  or set terraform outputs and run without args")
            sys.exit(1)
    else:
        wss = sys.argv[1]

    asyncio.run(main(wss))
