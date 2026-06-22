# My5

**Distributed NBA lineup simulation sandbox.** Pick any five players from the 2024-25 NBA season, pit them against any other five, and a Monte Carlo engine plays out tens of thousands of possessions — streaming live convergence progress back to the browser.

[DEMO GIF HERE]

[LIVE DEMO: url]

---

## What it is

My5 is a **sandbox for measuring relative lineup strength**, not a game-outcome predictor. It answers the question "which of these two lineups is likely better, and by how much?" — not "who wins Friday night."

You build two five-man lineups from 405 real players (aggregated from 52 games of 2024-25 NBA play-by-play). The simulator runs until the margin-of-victory confidence interval converges to a target width of ±2.0 pts — typically around 257 simulations — then pushes the final result and every intermediate progress frame over a WebSocket.

**What's modeled accurately:** individual player offensive tendencies (usage rate, shot-zone selection, FG% by zone, turnover rate, FT rate, OREB rate), blended against opponent defense via log5.

**What's modeled honestly but approximately:** novel lineups with no defensive history receive league-average defense via empirical Bayes shrinkage. The model says plainly when that's happening.

---

## Architecture

```
Browser (Next.js)
  │
  ├── POST /api/simulate ──► FastAPI (uvicorn local / Lambda+Mangum on AWS)
  │                               │
  │                         cache check ──HIT──► return instantly (1 DynamoDB read)
  │                               │
  │                             MISS
  │                               │
  │                        SQS / ElasticMQ ──► Job Worker
  │                                                  │
  │                                       Monte Carlo engine (NumPy)
  │                                       Welford variance, log5 blending
  │                                                  │
  │                                        write progress every 50 sims
  │                                        to my5-sim-jobs (DynamoDB)
  │                                                  │
  │          ┌─────────────────────────────────────-─┘
  │          │ LOCAL                         AWS
  │   NotifyingJobStore              DynamoDB Streams
  │          │                               │
  │      EventBus                    fanout Lambda
  │          │                               │
  │     WS server                API Gateway WebSocket
  │
  └◄──────────── progress + done frames ──────────────────
```

**Five DynamoDB tables, all PAY_PER_REQUEST ($0 idle):**

| Table | Purpose |
|---|---|
| `my5-lineup-metrics` | 1,819 reconstructed lineup records (defensive rates, pace) |
| `my5-player-params` | 405 player offensive parameters |
| `my5-sim-jobs` | Job lifecycle (QUEUED → RUNNING → DONE), live progress fields, TTL |
| `my5-sim-cache` | Result cache — sha256(sorted lineup pair + seed), 7-day TTL |
| `my5-ws-connections` | WebSocket connection registry (AWS only, GSI on job_id) |

**Dual-target design:** a single `infra/main.tf` with a `use_local` boolean variable targets either `amazon/dynamodb-local` + ElasticMQ (local Docker) or real AWS (us-east-1). The Python worker is byte-for-byte identical on both targets — only the endpoint URLs change via `MY5_ENV`.

---

## Engineering highlights

### $0-idle cost architecture

No always-on compute anywhere in the stack:

- DynamoDB PAY_PER_REQUEST → pay per read/write unit, $0 when idle
- Lambda scale-to-zero → billed per 100ms of invocation only
- SQS → $0.40/million requests (first 1M/month free)
- API Gateway WebSocket → $1/million connection-minutes
- No ElastiCache (see below), no NAT gateway, no VPC, no provisioned capacity

The AWS free tier comfortably covers this project's traffic at any realistic load.

### Cache layer: DynamoDB TTL, not Redis

The result cache (`my5-sim-cache`) is a DynamoDB table with a `ttl` attribute, not ElastiCache. ElastiCache minimum is a `cache.t4g.micro` cluster (~$13–16/month at idle). DynamoDB on-demand costs $0 at idle and ~1–5ms per read — equivalent to a Redis GET over a network hop. The access pattern (exact-key lookup, no sorted sets, no pub/sub) is a natural DynamoDB fit.

Cache key: `sha256(sorted([lineup_key_a, lineup_key_b]) + "|seed=" + seed)`. The sorted pair makes (A vs B) and (B vs A) hit the same entry. The seed is in the key because the engine is fully deterministic — a cache hit is a proven-equivalent replay, not an approximation. Non-deterministic runs (seed=None) are never cached.

Measured: **~43× hot-path speedup** (cache hit vs. cache miss), validated by the P3 load test against real AWS DynamoDB.

### Convergence-based stopping rule

The engine doesn't run a fixed number of simulations. It runs until `1.96 × sqrt(sample_var / n) ≤ 2.0 pts` AND `n ≥ 100`, hard-capped at 5,000 sims. Variance is tracked with Welford's online algorithm (constant memory, numerically stable). Progress frames fire every 50 sims — at typical convergence (~257 sims) the browser receives 5 progress frames before the final result.

### Determinism and stack transparency

Submitting the same lineup pair with the same seed through the full distributed stack (HTTP → SQS → worker → DynamoDB → WebSocket) produces a bit-identical result to a direct `simulate()` call. Verified in E2E tests: `stack vs direct delta = 0.00e+00`. This makes the cache semantically correct and makes bugs attributable — a divergent result signals a stack defect, not simulation noise.

### Possession model: per-possession Markov chain + log5 blending

Each possession is a finite-state machine:

1. **Ball-handler selection** — weighted by `usage_rate`
2. **Turnover check** — `log5(player.tov_rate, defense.forced_to_rate × conversion, lg)`
3. **FT trip check** — conditional `ft_rate / usage_rate` (no defensive metric in schema)
4. **Shot type** — rim / mid / 3p by player's shot-zone rates
5. **Make/miss** — `log5(player.zone_fg_pct, defense.allowed_zone_fg_pct, lg_avg)`
6. **Rebound** — OREB/DREB weighted, one capped putback on offensive board

Defensive rates use empirical Bayes shrinkage at read time: `(n × observed + prior_n × league) / (n + prior_n)`. At n=0 (novel lineup) this returns the league average exactly — no special case required.

Pace is coupled: one `Poisson(194)` draw sets total game tempo and is split evenly between teams. Independent pace draws inflated margin variance by ~43% and were eliminated.

### WebSocket push: record-as-truth design

The worker writes progress to `my5-sim-jobs` (DynamoDB) via an `on_progress` callback every 50 sims. On AWS, DynamoDB Streams trigger a `fanout_handler` Lambda that pushes frames to connected clients via the API Gateway Management API. The WebSocket is a display layer only — the job record is the durable source of truth. On socket disconnect or 60-second timeout, the frontend polls `GET /api/jobs/{job_id}` to recover state. This prevents the UI from hanging on network blips.

Local analog: `NotifyingJobStore` wraps `JobStore` and posts events to an `EventBus`, which bridges from the sync worker thread to the asyncio WebSocket server via `loop.call_soon_threadsafe`. The `handle_job` core is byte-for-byte identical on both targets.

### Observability (P3, AWS)

- **Structured logging:** one JSON line per job lifecycle event (`done`, `failed`, `cache_hit`) to stdout → CloudWatch Logs. Searchable via Logs Insights.
- **CloudWatch Embedded Metric Format (EMF):** `job_latency_ms` and `cache_hit_count` metrics emitted as JSON lines in the `My5/Simulator` namespace. No `PutMetricData` API calls — CloudWatch parses EMF from log data automatically.
- **X-Ray active tracing:** `tracing_config { mode = "Active" }` on both Lambdas. Captures per-invocation segments and automatic subsegments for every boto3 call (DynamoDB, APIGW). No `aws-xray-sdk` required.
- **CloudWatch dashboard:** Terraform resource (3 widgets: p99 latency, cache hit/miss count, DLQ depth). Created and destroyed with the stack.

---

## Honest limitations

| Limitation | Why it exists |
|---|---|
| **405 players from 52 games** | A player only appears if they played in the ingested games. Expanding to the full 1,230-game season would add more players and tighten lineup-level defensive estimates. |
| **Margin σ ≈ 16.6 pts vs. real-NBA σ ≈ 12 pts** | The two teams' scores are statistically independent — no shared within-game state (tempo adjustments, fatigue, foul trouble). Real games have r ≈ 0.3–0.5 between team scores, which would yield the observed 12 pts. Fixing it requires intra-game state this engine deliberately excludes. |
| **Novel lineups get league-average defense** | Defense is modeled at the lineup level, not per player. For any lineup with no historical data in the 52-game corpus, the shrinkage formula returns the league average (n=0 weight = 0). For hypothetical cross-team matchups this is the only honest option: per-player defensive fabrication is explicitly out of scope. |
| **All submissions use "hypothetical" lineup key** | The sandbox is designed for cross-team matchups where no shared lineup history exists. Historical defensive metrics would only apply when all 5 players played together on the same team in the ingested dataset. |

These aren't surprises discovered post-hoc — they're documented in [`DECISIONS.md`](DECISIONS.md) alongside the reasoning and planned mitigations.

---

## Tech stack

**Backend / data pipeline**
- Python 3.11, FastAPI, uvicorn, Mangum (Lambda adapter)
- numpy (Monte Carlo engine), polars (ETL)
- sportsdataverse-py (play-by-play data, returns polars DataFrames)
- boto3 (DynamoDB, SQS, API Gateway Management)
- websockets / FastAPI WebSocket (local WS server)

**Infrastructure / IaC**
- AWS: Lambda, SQS, DynamoDB, API Gateway WebSocket, CloudWatch EMF, X-Ray, IAM
- Local emulators: `amazon/dynamodb-local` (Docker), ElasticMQ (`softwaremill/elasticmq-native`)
- Terraform (single `infra/main.tf`, `use_local` variable for dual-target)

**Frontend**
- Next.js, TypeScript, Tailwind CSS v4

**Testing**
- pytest — 90 tests across 9 test modules (simulator, aggregation, reconstruction, cache, queue, WebSocket, AWS shell)

---

## Run locally

You need Docker, Python 3.11, and Node.js.

**Terminal 1 — local AWS emulators**
```bash
docker run --rm -d -p 8000:8000 --name dynamodb-local amazon/dynamodb-local
docker run --rm -d -p 9324:9324 --name elasticmq softwaremill/elasticmq-native:1.7.1

# Create DynamoDB tables (once per emulator restart)
terraform -chdir=infra apply -var="use_local=true" -auto-approve
```

**Terminal 2 — FastAPI HTTP layer**
```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"
MY5_ENV=local uvicorn api.main:app --reload --port 8001
```

**Terminal 3 — local WebSocket server**
```bash
MY5_ENV=local uvicorn my5.ws.server:app --port 8765
# [VERIFY: confirm this invocation with current server.py entry point]
```

**Terminal 4 — simulation worker**
```bash
MY5_ENV=local python -m my5.job_worker
```

**Terminal 5 — Next.js frontend**
```bash
cd frontend
cp .env.local.example .env.local   # sets API_URL=localhost:8001 and WS_URL=localhost:8765
npm install && npm run dev
# → http://localhost:3000
```

Run tests:
```bash
pytest
```

---

Built solo as a portfolio project — Python + systems, top to bottom.
