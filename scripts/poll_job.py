"""
Poll a simulation job record until done, printing live progress.

Usage:
    .venv/bin/python3 scripts/poll_job.py <job_id>

What it shows:
    Each line printed when progress_sims increases — i.e. each DynamoDB write
    fired by the engine's on_progress callback (every _PROGRESS_INTERVAL sims).
    Ends when status=done or status=failed.

This is the proof-of-concept for the progress feed before WebSockets. The same
DynamoDB fields (progress_sims, progress_ci) will be the data source for the
WebSocket push in the next phase.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from my5.job_store import JobStore


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: poll_job.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    store = JobStore()

    print(f"Polling {job_id[:8]}... (Ctrl-C to stop)")
    last_sims = -1

    while True:
        try:
            job = store.get_job(job_id)
        except KeyError:
            print(f"  Job {job_id!r} not found — has it been submitted?")
            sys.exit(1)

        status = job["status"]
        sims = int(job.get("progress_sims", 0))
        ci = job.get("progress_ci")

        if sims != last_sims and sims > 0:
            ci_str = f"±{float(ci):.2f} pts" if ci is not None else "..."
            print(f"  {sims:>5} sims  CI {ci_str}")
            last_sims = sims

        if status == "done":
            r = job["result"]
            print(f"\n  DONE after {int(r['n_sims'])} sims")
            print(f"    mean_margin      = {float(r['mean_margin']):+.3f} pts  (team A perspective)")
            print(f"    ci_half_width    = {float(r['ci_half_width']):.3f} pts")
            print(f"    equiv_net_rating = {float(r['equiv_net_rating']):+.1f} pts/100")
            print(f"    converged        = {r['converged']}")
            break
        elif status == "failed":
            print(f"\n  FAILED ({job.get('error_type')}): {job.get('error_message')}")
            sys.exit(1)
        elif status not in ("queued", "running"):
            print(f"\n  Unexpected status: {status!r}")
            sys.exit(1)

        time.sleep(0.5)


if __name__ == "__main__":
    main()
