# Design Decisions

Append a dated entry for every non-obvious choice. This is interview prep — keep it honest and concrete.

---

## 2026-06-15 — Data source: `sportsdataverse-py`

**Choice:** `sportsdataverse-py` (v0.0.59, the version pinned in `requirements.lock`) as the play-by-play data loader.

**Why it fits:**
- Returns polars DataFrames directly from a single call, so we skip writing and maintaining our own scraping/parsing layer.
- One interface covers both NBA (`sportsdataverse.nba`) and college (`sportsdataverse.mbb`), which we need for the cross-league feature later.
- Loads from pre-built release files (parquet), so a full season comes down in seconds instead of thousands of rate-limited per-game requests.

**Why not the alternatives:**
- `nba_api` (official-stats wrapper): returns JSON that has to be assembled across many endpoints, no polars support, and is rate-limited per game.
- Hand-rolled ESPN / stats.nba.com scraping: brittle to site changes and a maintenance burden we don't want to own.

**Important limitation — this is *why the spine exists*:** the bulk play-by-play does NOT include on-court lineups; it is event-level only. Reconstructing which five players are on the floor for each possession is our job, and is the core engineering of P1. For NBA, authoritative stint/rotation data exists separately and can be used to *validate* our reconstruction.

**Trade-off / risk:** `sportsdataverse` is a small open-source project with a low release cadence, so it could go unmaintained. Mitigation: we wrap it in a thin data-access layer, and the same underlying data is also published as parquet release files we could pull directly — so the reconstruction and simulation logic never has to change if we swap the loader out.

---

## 2026-06-15 — Two-datastore design: DynamoDB + Redis (no Postgres until P4)

**Choice:** DynamoDB for durable aggregates and player parameters; Redis/ElastiCache for hot state and the lineup-pair cache.

**Why DynamoDB (not Postgres) for durable storage:**
- The access patterns are (1) look up a lineup or player by ID and (2) read a small, narrow-range result set for a simulation job. Both are key/range reads — a natural fit for a DynamoDB single-table design, with no joins or ad-hoc queries needed in P1–P3.
- In **on-demand** billing mode, DynamoDB has no idle/always-on cost: you pay per request, and an unused table costs essentially nothing (just cheap storage). A small Postgres RDS instance runs ~$15–25/month even while idle. This is a personal student account, so idle cost matters.
- If P4 adds genuinely relational needs (saved lineups, salary-cap queries spanning many players), we introduce Postgres *then* — justified by a real requirement, not added up front "for range."

**Why Redis (not DynamoDB for everything):**
- Lineup-pair cache entries are hot, small, and short-lived. A repeat matchup result should return in well under a millisecond; Redis serves that pattern far faster than DynamoDB's single-digit-ms reads.
- The P2 live-progress feed needs pub/sub, which Redis provides natively. Achieving the same with DynamoDB Streams + Lambda would be slower and add moving parts.

**Trade-off acknowledged:** two stores means two SDKs, two operational surfaces, and two sets of failure modes. We accept that because the access patterns genuinely differ and the cost/latency wins are concrete, not résumé-driven. Three stores would be over-engineering; one store would force a bad fit on either the hot-cache or the durable-lookup path.

---

## 2026-06-15 — Reconstruction sort key: game_play_number (with tiebreakers), not sequenceNumber

**Finding:** `sequenceNumber` is NOT globally monotonic within a game. In the CHI@PHI game (401585087) there are 5 locations where sequenceNumber drops (e.g. row 83→84: seq 162→126). These are ESPN API artefacts — simultaneous or adjacent events get sequence numbers assigned in emission order, not strictly chronologically. `game_play_number` is a clean 1-N counter that is monotonically non-decreasing and was unique across all 500 rows we observed.

**Choice:** sort by `[game_play_number, sequenceNumber, original_row_index]`. The secondary and tertiary keys are defensive — they never fired in our test game, but they make the sort deterministic if ESPN ever emits duplicate `game_play_number` values in a different game.

---

## 2026-06-15 — Two invariants, and why count-only is insufficient

**The two invariants in `reconstruct.py`:**

**Invariant 1 (count):** after each substitution, each team's lineup must contain exactly 5 players. A violation means we received a sub event whose `player_out` was not in our tracked lineup — either a prior sub was silently dropped by the API, or the `player_out` ID doesn't match the roster.

**Invariant 2 (participant):** for every non-sub event, any participant attributed to the acting team must be a member of that team's reconstructed lineup at that moment.

**Why count alone is not enough:** It is possible for a lineup to contain exactly 5 players yet be wrong. Example: sub A-out / B-in is logged, but the lineup silently had C instead of A; after the swap the count is still 5, but C (who should have been swapped out) remains and A (who should have left) is gone. Invariant 2 catches this: if C then commits a foul, C won't be in the reconstructed lineup, and the violation surfaces immediately at the event where C acts. Without Invariant 2, the error would propagate silently for the entire stint.

---

## 2026-06-15 — Flag-don't-guess policy for dirty substitutions

**Policy:** when a substitution references a `player_out` who is not in the current reconstructed lineup, we: (1) mark the row `lineup_valid=False`, (2) log it with the `game_play_number`, and (3) add `player_in` to the lineup anyway so subsequent events have the best chance of being clean. We do NOT attempt to guess who should have been removed.

**Why not guess:** the most tempting heuristic is "remove the player with the fewest minutes" or "remove whoever shares a position," but these require assumptions we can't verify without external data. A wrong guess silently corrupts downstream stint metrics — a lineup that looks valid-by-count but contains a ghost player. By contrast, flagging cleanly lets aggregation logic downstream simply exclude flagged rows; dirty stints have no effect on final lineup metrics.

**Real-game result (CHI@PHI, 401585087):** After fixing the `active` flag bug (see below), 499 of 500 rows passed both invariants. The single flag is gpn=180: a "Kicked Ball Turnover" credited to Terry Taylor (4279815), but the immediately preceding sub (gpn=179) replaced Taylor with DeRozan, so Taylor was already off the floor by gpn=180. This is an ESPN data inconsistency — the turnover clock position (seq=266) falls after the sub (seq=259) in `game_play_number` order but the event text still names Taylor. Invariant 2 correctly catches this.

---

## 2026-06-15 — ESPN roster active flag is unreliable; filter on did_not_play instead

**Finding:** `espn_nba_game_rosters()` returns an `active` boolean that reflects contract/eligibility status, not "dressed tonight." In the CHI@PHI game, 10 of the 11 players marked `active=False` actually appeared in substitution events (Patrick Beverley, Terry Taylor, Danuel House Jr., Jaden Springer, Marcus Morris Sr., and others on hardship exceptions or two-way deals). Only one `active=False` player (Torrey Craig) was a genuine DNP, and he also had `did_not_play=True`.

**Choice:** build the player→team map by filtering on `did_not_play=False` only, ignoring `active`. This is the only flag in the roster that reliably identifies a player who never entered the game.

---

## 2026-06-15 — PBP loader must use infer_schema_length=None

**Root cause:** `pl.from_dicts()` infers column types from only the first N rows (polars default ~100). This is an inference window problem, not specific to any event type. If a field is null in the first ~100 rows but populated by a rare event later in the file, polars types it as `Null`, then raises a schema conflict when the real value appears.

**Trigger found:** `participants.2.athlete.id` — populated only on jump-ball events where a third participant is named (the player who gains possession). In SAS@MEM (game 401585088), the first such jump ball was at row 167, past the inference window; polars had already committed the column type as `Null`.

**Affected fields (across 52 games):** scanning all cached PBP parquets, `participants.2.athlete.id` is the only field that exhibits late-population. No other field (coordinate, score, clock, team ID) was null in the first 100 rows but populated later.

**Fix:** `pl.from_dicts(plays_dicts, infer_schema_length=None)` — forces polars to scan all rows before committing the schema. This is now centralised in `src/my5/loader.py:load_pbp()`. Both scripts that previously called `pl.from_dicts()` directly now go through `load_pbp()`. The fix was verified by re-running the full 52-game batch: **52/52 loaded and reconstructed without crashing** (previously 51/52).

**Open question for aggregation:** when `lineup_valid=False` is set because of the post-sub attribution pattern (gpn=180, gpn=195 — event credited to a player who was subbed out within the same clock-second), should aggregation exclude just that single row, or the entire stint containing that row? The single-row exclusion is more permissive and loses almost nothing; the stint exclusion is conservative but discards valid surrounding events. Decide before building the aggregator.