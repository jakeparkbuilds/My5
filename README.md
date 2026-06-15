# My5

Basketball lineup simulation sandbox. Build any five-man unit, pit it against any other (NBA or college, any era), and a Monte Carlo engine plays out tens of thousands of possessions, streaming the outcome distribution back live.

**Status:** P1 — local ETL spine (in progress)

## Setup

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Phases

- **P1 (now):** Ingest play-by-play → reconstruct lineups → aggregate metrics → minimal frontend
- **P2:** Monte Carlo simulator + async job queue + WebSocket streaming
- **P3:** Caching + observability + load test
- **P4 (optional):** Cross-era normalization + auth/saved lineups
