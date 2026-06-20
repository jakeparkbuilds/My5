"""
Structured logging and CloudWatch EMF metric emission.

Two helpers are exported:
  log_job_event(job_id, event, **fields) — JSON line to stdout (human + machine readable)
  emit_emf(metrics, dimensions, **extra)  — CloudWatch Embedded Metric Format to stdout

EMF explained
-------------
CloudWatch Embedded Metric Format (EMF) is a structured JSON payload printed to
stdout. When a Lambda function logs to CloudWatch Logs, the Logs service auto-parses
any EMF line and ingests the named metrics into CloudWatch Metrics — no PutMetricData
API calls, no extra SDK, no cost beyond normal log ingestion.

Format reference: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

On LOCAL runs, EMF output goes to the terminal and is ignored by CloudWatch (no
Lambda, no log group). It still appears as readable JSON for debugging.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

ENV: str = os.getenv("MY5_ENV", "local")

_NAMESPACE = "My5/Simulator"


def log_job_event(job_id: str, event: str, **fields: Any) -> None:
    """
    Emit one structured JSON line for a job lifecycle event.

    Lambda stdout → CloudWatch Logs → searchable via Logs Insights.
    Locally: prints to terminal for debugging.
    """
    record = {
        "ts": int(time.time() * 1000),
        "job_id": job_id,
        "event": event,
        "env": ENV,
        **fields,
    }
    print(json.dumps(record), flush=True)


def emit_emf(
    metrics: dict[str, tuple[float | int, str]],
    dimensions: dict[str, str],
    **extra: Any,
) -> None:
    """
    Emit one CloudWatch EMF record to stdout.

    metrics:    {"metric_name": (value, "Unit")}
                Supported units: Milliseconds, Count, Percent, None
    dimensions: {"dim_name": "dim_value"} — keys used to slice metrics in CloudWatch
    **extra:    Additional fields included in the log record (searchable, not metrics)

    CloudWatch parses EMF lines from Lambda logs automatically.
    Pricing: standard Logs ingestion ($0.50/GB); metric storage free up to 10 metrics.
    X-Ray free tier: 100K traces/month → $0 at our scale.
    """
    metric_defs = [{"Name": k, "Unit": u} for k, (_, u) in metrics.items()]
    dim_keys = list(dimensions.keys())

    emf: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": _NAMESPACE,
                    "Dimensions": [dim_keys],
                    "Metrics": metric_defs,
                }
            ],
        },
        **dimensions,
        **{k: v for k, (v, _) in metrics.items()},
        **extra,
    }
    print(json.dumps(emf), flush=True)
