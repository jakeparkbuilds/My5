# Design Decisions

Append a dated entry for every non-obvious choice. This is interview prep — keep it honest and concrete.

---

## 2026-06-15 — Data source: `sportsdataverse-py`

**Choice:** `sportsdataverse-py` as the play-by-play data loader.

**Why not alternatives:**
- `nba_api` (official NBA API wrapper) returns JSON and requires assembling play-by-play manually across many endpoints; no polars support; rate-limited.
- Raw ESPN / Stats NBA scraping is fragile and violates ToS risk.
- `sportsdataverse` returns polars DataFrames directly from a single call, covers both NBA (`sportsdataverse.nba`) and college (`sportsdataverse.mbb`) under one interface, and is the library the CLAUDE.md spec names explicitly.

**Trade-off acknowledged:** `sportsdataverse` is a small open-source project (latest PyPI release 0.0.59 as of June 2026) — it could go unmaintained. If that happens, the thin data-access layer we're building around it means we could swap loaders without touching the reconstruction or simulation logic.

---

## 2026-06-15 — Two-datastore design: DynamoDB + Redis (no Postgres until P4)

**Choice:** DynamoDB for durable aggregates and player parameters; Redis/ElastiCache for hot state and lineup-pair cache.

**Why DynamoDB (not Postgres):**
- We have two main access patterns: (1) lookup a lineup or player by ID, (2) scan a small result set for a simulation job. Both are key-value or narrow-range reads — a perfect fit for DynamoDB's single-table design.
- DynamoDB is serverless, scales to zero on-demand, and has no always-on cost at low RCU/WCU. A Postgres RDS instance costs ~$15–25/month idle. This is a student account.
- No complex ad-hoc queries during P1–P3. If we add them in P4 (saved lineups, salary-cap queries across players), we add Postgres then.

**Why Redis (not just DynamoDB for everything):**
- Lineup-pair cache entries are hot, small, and ephemeral — a simulation result for "(LeBron, AD, …) vs (Curry, Klay, …)" should be served in <1 ms on repeat requests. DynamoDB single-digit-ms latency is fine for cold lookups but Redis is 10–100× faster for this pattern.
- WebSocket live-progress state (P2) needs pub/sub; Redis has it natively. DynamoDB Streams + Lambda would be slower and more complex.

**Trade-off acknowledged:** Two datastores means two things to operate, two SDKs, two sets of failure modes. We accept this because the access patterns genuinely differ and the cost/latency benefits are concrete, not speculative.
