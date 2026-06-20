"""
Shared WS message emitter: job record dict → WebSocket message dict.

Used by two callers:
  - ws/server.py  ws_handler snapshot (local path, plain Python dict from JobStore)
  - ws/aws/fanout_handler.py  (AWS path, dict from TypeDeserializer on Streams NewImage)

Factored here so neither caller can diverge on message shape. The shared-emitter
test in test_ws_aws.py asserts server._job_to_message IS this function.

Both callers produce the same input shape:
  - Numeric fields are int/float (local) or Decimal (Streams after TypeDeserializer).
  - int(), float(), bool() coercions below handle both without branching.
"""
from __future__ import annotations

from typing import Any


def job_record_to_message(job: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a job record to a WebSocket message dict.

    Message types:
      done     — status="done";   carries full SimResult fields from result map
      failed   — status="failed"; carries error_type + error_message
      progress — status=queued|running; carries sims_done + ci_half
    """
    status = job.get("status", "queued")

    if status == "done":
        r = job["result"]
        return {
            "type": "done",
            "n_sims": int(r["n_sims"]),
            "mean_margin": float(r["mean_margin"]),
            "ci_half_width": float(r["ci_half_width"]),
            "equiv_net_rating": float(r["equiv_net_rating"]),
            "converged": bool(r["converged"]),
        }

    if status == "failed":
        return {
            "type": "failed",
            "error_type": job.get("error_type", "unknown"),
            "error_message": job.get("error_message", ""),
        }

    # queued or running — latest progress snapshot (zeros if not started yet)
    sims = job.get("progress_sims")
    ci = job.get("progress_ci")
    return {
        "type": "progress",
        "sims_done": int(sims) if sims is not None else 0,
        "ci_half": float(ci) if ci is not None else 0.0,
    }
