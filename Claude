# My5 — Project Context for Claude Code

## What this is
My5 is a distributed basketball lineup-simulation sandbox. You build any five-man
unit, pit it against any other (NBA or college, any era), and a Monte Carlo engine
plays out tens of thousands of possessions across a worker fleet, streaming the
outcome distribution back live. Historical lineups are reconstructed from millions
of play-by-play events; hypothetical matchups are simulated from an empirically
parameterized possession model. It is a SANDBOX, not a forecaster — the engineering
is the point, not the prediction.

This is a solo portfolio project for a rising-junior CS+math student targeting
big-tech SWE/DS/ML roles. The goal is to demonstrate ML depth + systems range:
real distributed compute, async jobs, real-time WebSockets, IaC, CI/CD, and a
defensible two-datastore design.

## How to work with me (read this carefully)
- I am a student and I must UNDERSTAND everything we build well enough to defend it
  in an interview. Teaching me beats impressing me.
- After any non-trivial change, explain WHAT you did and WHY in plain language.
- Make small, reviewable changes — one concern at a time. I read every diff.
- When there is a real design decision (more than one reasonable option), STOP and
  lay out the tradeoff before choosing. Don't silently pick.
- Maintain DECISIONS.md: append a short dated entry for every non-obvious choice
  (why X over Y). This file is my interview prep — keep it honest and concrete.
- Never introduce a library, service, or abstraction without telling me what it is
  and why we need it. No speculative complexity.

## Phasing — DO NOT SKIP AHEAD
Build strictly in this order. Each phase must work end-to-end before the next.
- **P1 (NOW): the spine.** Ingest play-by-play → reconstruct on-court lineups →
  aggregate lineup metrics + per-player simulator parameters → minimal frontend
  showing real historical lineup stats. Local-first.
- **P2: the simulator.** Monte Carlo possession model + async job queue + WebSocket
  live progress.
- **P3: performance.** Caching layer + observability dashboard + load test.
- **P4 (optional): upside.** Cross-era normalization + auth/saved-lineups/salary-cap
  game. This is the ONLY thing that introduces Postgres.
If I ask you to jump ahead (e.g. start the simulator while P1 is unfinished),
remind me of this section first.

## Local-first rule (critical for cost AND sanity)
- Develop and TEST all business logic (ETL, reconstruction, aggregation, simulator)
  locally as plain Python first.
- Do NOT move logic to AWS until it is correct and tested locally. Never debug
  business logic by redeploying to the cloud.
- Iterate on SMALL data slices (a few games / one season), never the full dataset.
- Keep cloud-specific code behind thin interfaces so the logic stays testable locally.

## Cost guardrails (this runs on a student's personal AWS account)
- Before provisioning ANY AWS resource, tell me: does it cost money while idle?
  Rough monthly cost? Does it scale to zero?
- Flag always-on costs loudly — e.g. ElastiCache nodes, NAT gateways, idle Fargate
  tasks. Suggest the cheapest thing that works.
- Prefer serverless / scale-to-zero / on-demand. Always give me tear-down steps for
  anything spun up to test.
- We are NOT touching AWS during early P1 — local only. I'll set a billing alarm
  before the first cloud resource; remind me when we get there.

## Tech stack (don't deviate without asking)
- Backend / ETL / sim: Python 3.11+. Frontend: TypeScript + Next.js (P1 frontend
  can be bare-minimal).
- Data source: `sportsdataverse-py` (`sportsdataverse.nba`, `sportsdataverse.mbb`;
  loaders return polars). Start with NBA, small slices. College is a later expansion.
- Data wrangling: polars preferred (pandas acceptable).
- Datastores: DynamoDB (durable aggregates + player params) + Redis/ElastiCache
  (hot state + lineup-pair cache). NO third store. NO Postgres before P4.
- Async/compute: SQS + Lambda/Fargate. IaC: Terraform (or CDK). CI: GitHub Actions.
  Observability: CloudWatch + X-Ray + dead-letter queue.

## Modeling rules (the honest design)
- Defense is modeled at the LINEUP level, never per-player. Do not fabricate
  per-player defensive ratings — the opponent five contributes aggregate
  FG-allowed / forced-TO / defensive-rebound rates.
- Possession = finite-state Markov chain. Transition probs blend offense tendency
  × opponent tendency / league baseline via log5.
- Monte Carlo stopping rule = run until the margin-of-victory confidence interval
  tightens to a target width — not a fixed possession count.
- Aggregation output IS the simulator's input. Keep the two halves connected; that
  is what makes the architecture cohere.

## Validation (build this in — do not skip)
- Hold out real historical lineups and check the simulator reproduces their actual
  net rating within the CI.
- Where NBA stint/rotation data is available, validate reconstructed lineups against
  it (authoritative on-court data exists for NBA; college must be inferred).

## Conventions
- Clear names over cleverness. Type hints. Docstrings on non-obvious functions.
- Unit-test the fiddly logic — reconstruction edge cases especially — with known
  inputs/outputs.
- Small, frequent commits with meaningful messages.
- .gitignore: data files, .env, venv, AWS creds. Never commit data or secrets.
- Pin dependencies (requirements lock or pyproject + lockfile).

## Current status
P1, day 0. Empty repo. Next step: scaffold, then a data-access spike to learn the
play-by-play schema before building anything.