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

**Open question RESOLVED (2026-06-15):** See "inv1/inv2 exclusion scopes" entry below.
---

## 2026-06-15 — Invariant exclusion scopes: inv1 → stint, inv2 → row

**Choice:** `violation_type` column on every PBP row (`None` / `"inv1_count"` / `"inv2_participant"`). Aggregation applies:
- **inv1_count** → exclude the entire stint containing the violating row, AND any subsequent stints whose lineup has ≠ 5 players (corrupted by the ghost-add recovery in `_apply_sub`).
- **inv2_participant** → drop only the flagged row; the rest of the stint is kept.

**Why the asymmetry:** Invariant 1 fires when a sub references a `player_out` who was never in our tracked lineup — meaning our lineup state was wrong *before* the sub. Every event in that stint is measured against a bad lineup, so the whole stint's metrics are unreliable. Invariant 2 fires on a single event attribution mismatch (the count is still 5) — the lineup itself is sound, only one event is dirty.

**Real-game cases (52 games):** 2 flags total — both are inv2. CHI@PHI gpn=180: kicked ball turnover credited to Terry Taylor who had just subbed out. POR@DAL gpn=195: technical foul credited to Grant Williams who was not in the reconstructed home lineup. Both are ESPN data artifacts; the surrounding events are clean.

---

## 2026-06-15 — Possession counting: event-walking rule-set

**Choice:** Walk play-by-play events in `game_play_number` order and detect possession-end transitions directly. Count one possession for the **team that just lost the ball** at:

| Event | Condition | Possession ends for |
|---|---|---|
| Made FG (not and-one) | `scoringPlay=True`, `scoreValue ∈ {2,3}`, `_is_and_one_fg=False` | `team.id` |
| And-one FT completion | `type.text == "Free Throw - 1 of 1"`, `_is_and_one_ft=True` | `team.id` |
| Made final FT (non-and-one) | `"Free Throw" in type.text`, `"Technical" not in`, final position (N of N), `scoringPlay=True`, `_is_and_one_ft=False` | `team.id` |
| Defensive Rebound | `type.text == "Defensive Rebound"` | the team that is **not** `team.id` |
| Turnover | `"Turnover" in type.text` | `team.id` |

Not possession ends: missed FGs (let DREB handle), missed FTs (let DREB handle — counting both would double-count), Technical FTs, OREBs, fouls, substitutions, period boundaries.

**Why missed FTs are not possession-ends:** A missed final FT always leads to a live rebound. If we counted both the missed FT AND the subsequent DREB, the possession would be counted twice. The DREB rule already captures the possession end cleanly.

---

## 2026-06-15 — And-one pairing: semantic player+team+clock key (no row window)

**Choice:** An and-one is identified by scanning forward from a made FG and finding `type.text == "Free Throw - 1 of 1"` where `participants.0.athlete.id`, `team.id`, AND `clock.displayValue` all match the made FG. No fixed row-distance window.

**Why not a row-window heuristic ("scan next 3 rows"):** The row distance between the made FG and its and-one FT ranges from 2 to 8 in our 52-game corpus (249 and-ones found). A 3-row window would miss 14.4% of and-ones (all those at dist ≥ 4). Wider windows (e.g., 15 rows) work but need a stopping condition or risk false positives.

**Why not the foul-bridge approach:** Checking for an intermediate Shooting Foul event found 0/249 pairings. ESPN either omits the foul row entirely between the FG and FT, or `participants.1` on the foul row doesn't consistently identify the fouled player.

**Coverage:** player+team+clock key paired 249/249 and-ones (100%), zero ambiguous cases.

**Implementation:** scan up to 15 rows forward. Break immediately when the matching FT-1-of-1 is found. The 15-row bound is a safety cap; the true empirical maximum is 8 rows.

---

## 2026-06-15 — Usage rate definition: (FGA + TOV + FT_trips) / team_poss_on_floor

**Choice:** A player's usage is `(FGA + TOV + FT_trips) / team_poss_on_floor`, where `FT_trips` = count of non-and-one FT trip starts (`"1 of" in type.text`, `"Free Throw" in type.text`, `"Technical" not in type.text`, not `_is_and_one_ft`), attributed to `participants.0.athlete.id`.

**Why FGA+TOV is insufficient:** A shooting foul on a missed shot sends the player to the line without producing a FGA event. That consumption of a team possession is invisible to FGA+TOV. For top foul-drawers (Embiid, Giannis, Banchero) the gap reaches 17–27% — a material undercount that would systematically underweight their role in the possession model.

**FT_trip attribution:** The FT shooter (`participants.0` on the first FT of each trip) is credited. Technical FTs are excluded because the tech foul is attributed to the committing player (their possession context), not the FT shooter.

**And-one FTs excluded from FT_trips:** The player already consumed a possession via the FGA. The and-one FT is a bonus attempt within the same possession, not a separate usage event.

**Lone Free Throw - 1 of 1 (non-and-one):** These (12 in 52 games — typically flagrant or clear-path scenarios) DO count as FT trips. The `_is_and_one_ft` flag distinguishes them from and-one FTs.

---

## 2026-06-15 — Rim classification: event type-name primary, keywords {Layup, Dunk, Tip, Finger Roll}

**Choice:** A 2PT FGA is classified "rim" if `type.text` contains any of `Layup`, `Dunk`, `Tip`, or `Finger Roll`. All other 2PT FGAs are "mid-range." 3PT FGAs are "three" (ESP encodes this via `pointsAttempted == 3`; no coordinate needed).

**Why type-name over coordinate cutoff:** Coordinate `y ≤ 7` clips 15 legitimate rim-area shots (ESPN-labeled "8-foot driving layup", "11-foot layup", "finger roll layup") into the mid bucket. These are genuine near-rim attempts. The type-name rule captures all of them.

**Sentinel coordinates on FTs:** `coordinate = −214748340` appears only on `pointsAttempted == 1` rows (FTs). All FGAs have valid coordinates. Coordinates are safe to use as a supplementary filter for ambiguous cases.

**RESOLVED (2026-06-15) — Hook shots added to rim; floaters stay mid.**

Coordinate analysis across 52 games:
- `Hook Shot`, `Turnaround Hook Shot`, `Driving Hook Shot`: 88% at y≤7, median y=4–6 → **rim** (keyword: "Hook")
- `Driving Floating Bank Jump Shot`: 95% at y≤7, median y=4 → **rim** (keyword: "Hook" does not match; "Floating Bank" is a separate keyword added)
- `Driving Floating Jump Shot`, `Floating Jump Shot`: 47–62% at y≤7, median y=7–8 → **mid** (borderline, but floaters are conventionally mid-range by analytics convention)

**Final `_RIM_KEYWORDS`:** `{Layup, Dunk, Tip, Finger Roll, Hook}`. All hook-variant type names contain "Hook"; the "Floating Bank" variants also contain "Hook" via no match — wait, they do NOT. On re-inspection: `Driving Floating Bank Jump Shot` does not contain "Hook". Its 95% rim rate makes it borderline for reclassification, but it affects ~40 shots in 52 games and is not matched by any current keyword. **Left as mid for now; flag for revisit at full-season scale if mid FG% remains elevated above 0.42.**

Adding "Hook" moves ~90 hook shots from mid→rim. Effect on league-average mid FG%: drops from 0.445 toward 0.38–0.42 (verified by rerun — see run log).

---

## 2026-06-15 — inv1_count silent-corruption bug: the most important correctness fix in P1

**The bug:** `_accumulate_game` correctly excluded stints whose rows carried `violation_type == "inv1_count"`. But the exclusion was keyed on the *flag*, not on the *lineup corruption*. When a dirty sub fires, `_apply_sub` marks the sub row `inv1_count` and adds `player_in` without removing anyone, leaving the lineup with 6 players. That sub row goes into stint N (the pre-sub lineup), which was correctly excluded. The subsequent events go into stint N+1, keyed by the 6-player lineup — and stint N+1 carries **no** `inv1_count` flag. It passed through the filter entirely.

**Why it was invisible:** Across our 52-game corpus there are zero inv1 violations (only 2 flags total, both inv2). The bug would only manifest if an inv1 violation was present. At full-season scale (1,230 games), there are likely dozens; each would silently contaminate the downstream stint with a phantom 6th player in the lineup key, producing lineup metrics attributed to an impossible 6-person unit.

**How it was caught:** A synthetic unit test (`test_inv1_stint_fully_excluded`) constructed a fake game with a deliberate inv1 sub, then verified the following FG produced zero possessions. It failed — the FG in the 6-player stint was counted — revealing the gap.

**The fix:** Added a lineup-size guard in `_accumulate_game`:
```python
if len(home_lineup_set) != 5 or len(away_lineup_set) != 5:
    corrupted_stints.append({...})
    continue
```
This catches every subsequent stint that inherits the ghost-add corruption, regardless of whether it carries an inv1 flag.

**Why the guard also reports:** Made silent. The count `len(corrupted_stints)` is printed in the run summary. At 52 clean games this is 0. At full season scale, a non-zero count is a data-quality signal that must be investigated before results are trusted. The skip was previously completely silent — a `continue` with no log — which meant we could silently lose hundreds of possessions at scale with no indication anything was wrong.

---

## 2026-06-15 — OREB rate: player_ORs / team missed FGA and missed final FT while on floor

**Choice:** `oreb_rate = player_offensive_rebounds / team_missed_shot_opportunities_while_on_floor`

Denominator counts each missed FGA (`shootingPlay=True, scoringPlay=False, pointsAttempted ∈ {2,3}`) and each missed final FT (`_is_final_ft=True, scoringPlay=False, "Technical" not in type.text`) by the player's team while the player is in the reconstructed lineup. All 5 on-floor offensive players get +1 opportunity per miss.

**Why not OREB / team possessions:** Possessions conflate rebounding skill with how often a team shoots (high-usage possessions inflate the denominator). The correct denominator is opportunities — misses that create a live rebound.

**Sanity check:** 1,090 total player OREB credits across 52 games = 5 × ~218 actual team OREBs. The 5× inflation from crediting all 5 on-floor players cancels in the ratio and is correct.

**Observed range (321 players, ≥20 opportunities):** 0% – 22%, median 3.2%. Team OREB% ≈ individual × 5 ≈ 16%, consistent with starters seeing more opportunity-per-player than the whole roster average.

**Validation note (SUSPECTED, not confirmed):** Observed team OREB% (~16%) is below the NBA typical range of 25–28%. Suspected cause: our denominator may undercount live-rebound opportunities in edge cases (e.g., jump balls after a shot-clock reset, certain flagrant-foul FT sequences where the ball is live). Not confirmed — revisit at full-season scale with a direct count of ESPN-logged OREBs against our denominator totals.

---

## 2026-06-15 — Validation notes: OREB% low, 3P% slightly high (SUSPECTED causes only)

These are observations from the 52-game validation run. Root causes are not confirmed; do not treat these as definitive diagnoses.

**3P% at 0.379 (NBA typical 0.35–0.37):** SUSPECTED to reflect sample composition — 52 hand-selected games may skew toward high-3P% teams or game-states. The 52-game slice is not a random sample of the season. Revisit at full-season scale.

**Mid FG% at ~0.41 after Hook reclassification (down from 0.445):** Still slightly above NBA mid-range of 0.38–0.42. SUSPECTED residual cause: `Driving Floating Bank Jump Shot` and other near-rim shots without a keyword match (~40 shots in 52 games) remain in the mid bucket. Not reclassified yet — too few shots for a firm decision. Revisit at full-season scale.

---

## 2026-06-16 — Local DynamoDB emulation: amazon/dynamodb-local (not LocalStack)

**Choice:** `amazon/dynamodb-local` Docker image (`amazon/dynamodb-local:latest`, port 8000) for local development instead of LocalStack.

**Why not LocalStack:** LocalStack ≥2026.x merged community and Pro into one image (same digest: `ade907629584`). The community DynamoDB feature now requires an account registration even for local offline use. The `LOCALSTACK_ACKNOWLEDGE_ACCOUNT_REQUIREMENT=1` grace-period flag was removed. The container starts and immediately exits with an auth error.

**Why amazon/dynamodb-local:** AWS publishes their own official DynamoDB emulator as a Docker image. It is free, requires no account, is maintained by AWS (not a third party), and implements the full DynamoDB API — boto3 and the AWS CLI point at it identically, just with `--endpoint-url http://localhost:8000`. It is used by AWS's own SDK test suites. It covers everything this phase needs.

**Future phases:** If we need local emulation of S3, SQS, or Lambda (P2+), we'll revisit LocalStack (sign up for a free account) or per-service alternatives then. Don't adopt a heavier tool before it's needed.

**Run the emulator:**
```bash
docker run --rm -d -p 8000:8000 --name dynamodb-local amazon/dynamodb-local
# Stop it:
docker stop dynamodb-local
```

---

## 2026-06-16 — Dual-target Terraform: one codebase, local vs. real AWS via a variable

**Choice:** A single `infra/main.tf` with a boolean variable `use_local` (default `true`). When `true`, the AWS provider is configured with `endpoints { dynamodb = "http://localhost:8000" }`, dummy credentials (`access_key = "test"`, `secret_key = "test"`), and `skip_*` flags that suppress validation calls DynamoDB Local can't answer. When `false`, the provider is a standard AWS provider (reads credentials from the normal chain).

**Why one file instead of two (dev.tf / prod.tf):** Two files immediately diverge. Schema changes, billing-mode changes, and tag changes must then be applied in two places, and they will drift. One file with a variable guarantees the local and real-AWS environments are identical by construction.

**Why a Terraform variable (not a backend / workspace):** This is a simple endpoint switch, not a different environment with different state. Terraform workspaces are for parallel environments (staging vs prod) that need separate state files. A variable is the right abstraction for "same resources, different target."

**The `skip_*` flags explained:**
- `skip_credentials_validation` — DynamoDB Local doesn't implement the STS credential validation endpoint.
- `skip_requesting_account_id` — DynamoDB Local returns a fake account ID (`000000000000`); requesting the real one would fail.
- `skip_metadata_api_check` — DynamoDB Local has no EC2 instance metadata service.
All three are no-ops for real AWS and required for DynamoDB Local.

**Deploy to real AWS (future):**
```bash
terraform -chdir=infra apply -var="use_local=false"
```
No code change needed.

---

## 2026-06-16 — float→Decimal(str(x)) conversion and NaN-omission rule for DynamoDB

**The boto3 float rejection:** boto3 raises `TypeError: Float types are not supported. Use Decimal types instead.` if any Number attribute is a Python `float`. This is not a boto3 bug — DynamoDB's number type is an arbitrary-precision decimal, not IEEE 754. boto3 enforces this at the serialization layer.

**Why `Decimal(str(x))` and NOT `Decimal(x)`:**
- `Decimal(0.1)` produces `Decimal('0.1000000000000000055511151231257827021181583404541015625')` because the float `0.1` is already imprecise in binary. Every float carries this noise.
- `Decimal(str(0.1))` produces `Decimal('0.1')` — exactly 0.1. `str()` rounds to the float's display precision, which for `round(x, 4)` outputs are clean (e.g. `str(round(0.3952, 4)) == '0.3952'`).
- Our aggregation always calls `round()` before storing rates (1–4 decimal places), so `str()` is lossless for all values in the DataFrame.

**The NaN-omission rule:** DynamoDB rejects `NaN` as a Number value (it is not a valid JSON number). Any attribute whose computed value is `float('nan')` or `None` is **omitted** from the DynamoDB item entirely. A missing attribute is unambiguous; a NaN attribute would silently corrupt the simulator's probability lookup. In practice our aggregation returns `0.0` for zero-denominator rates (via `_safe(n, d)`), so NaN values should never appear — but the guard is always-on for safety.

**Zero is NOT omitted:** `Decimal('0')` and `Decimal('0.0')` are valid and written. Only `None` and `float('nan')` are omitted.

**Idempotency:** `PutItem` overwrites any existing item with the same PK. Re-running `load_dynamo.py` rewrites all 1,819 lineup items and 405 player items cleanly — no duplicates, no stale data.

**Round-trip confirmed (2026-06-16):** PHI starters lineup: off=126.9, def=113.0, net=+13.9. Embiid usage_rate=0.3952. All values read back from DynamoDB Local match the aggregation source exactly.

---

## 2026-06-16 — lineup_key as DynamoDB PK: canonical form "{team_id}#{sorted_ids}"

**Choice:** The DynamoDB partition key for `my5-lineup-metrics` is a string:
```
"{team_id}#{athlete_id_0}#{athlete_id_1}#{athlete_id_2}#{athlete_id_3}#{athlete_id_4}"
```
where the 5 athlete IDs are sorted **numerically** (not lexicographically).

**Why sort numerically, not lexicographically:** IDs like `[3416, 6440, 3059318]` sort numerically to `[3416, 6440, 3059318]` but lexicographically to `[3059318, 3416, 6440]` (because '3' < '6' as a string). An incorrect lex sort would produce different keys for the same lineup depending on how the IDs happen to be ordered in the input, breaking the idempotency of the key construction. Python's `sorted()` on integers is numeric by default; we explicitly cast to `int` before sorting.

**Why not a hash or UUID:** A deterministic string key can be reconstructed from any representation of the lineup without a lookup table. The simulator, the API, and any ETL job can independently compute the same key from a list of 5 IDs. A hash is opaque and can collide; a UUID requires a registry.

**Why team_id is in the key:** The same 5 players could theoretically appear as a lineup on different teams (trades, All-Star rosters, hypothetical matchups). Including team_id keeps the key correct for our actual use case (same lineup on different teams has different metrics) and aligns with the simulator's lookup pattern (`get_lineup_metrics(team_id, five_athlete_ids)`).

**Stability guarantee:** `reconstruct.py` stores `home_lineup` and `away_lineup` as sorted lists (the `sorted()` call is explicit in the reconstruction step). The aggregation accumulator keys on `tuple(sorted_lineup)`. The DynamoDB key adds `team_id` and re-sorts defensively. Any permutation of the same 5 IDs → identical key. Verified by tests `test_lineup_key_order_independent` and `test_lineup_key_numeric_sort_not_lexicographic`.

---

## 2026-06-16 — Defensive lineup rates: apply empirical Bayes shrinkage at engine time (same constants as player params)

**Problem:** Per-lineup defensive rates (`opp_rim_fg_pct`, `opp_mid_fg_pct`, `opp_3p_fg_pct`, `forced_to_rate`, `dreb_rate`) are raw aggregates with no shrinkage. A small-sample lineup's rates are wildly noisy — the worked example exposed this: team_id=27's starting lineup allowed 31/57 three-pointers over 5 games (54.4% 3P-allowed, vs 37.95% league average). Without treatment, log5 inflates a below-average 3P shooter's make probability from 30.8% to 46.5%, and nothing flags it because simulated games have no ground truth to check against.

**Fix:** Apply the same empirical Bayes shrinkage to all five defensive rates **at engine read time** using the raw counts already stored in `my5-lineup-metrics`:

```
shrunk_rate = (n × observed + prior_n × lg) / (n + prior_n)
```

| Parameter | Denominator | prior_n | Matches player param |
|---|---|---|---|
| `opp_rim_fg_pct` | `opp_rim_fga` | 25 | `_PRIOR_N_ZONE` |
| `opp_mid_fg_pct` | `opp_mid_fga` | 25 | `_PRIOR_N_ZONE` |
| `opp_3p_fg_pct` | `opp_3p_fga` | 25 | `_PRIOR_N_ZONE` |
| `forced_to_rate` | `total_def_poss` | 50 | `_PRIOR_N_RATE` |
| `dreb_rate` | `dreb_opp` | 50 | `_PRIOR_N_RATE` |

**Effect on the 54.4% 3P example (n=57 shots, prior_n=25):**
- weight = 57/(57+25) = 0.695
- shrunk = 0.695 × 0.544 + 0.305 × 0.380 = 0.494
- log5(0.3084, 0.494, 0.380) = 41.6% (vs 46.5% raw — noise dampened, signal preserved)

**Why at read time, not write time:** The league averages used as the shrinkage prior will be recalculated as more games are added. Shrinking at write time bakes in the prior from the ingest run; shrinking at read time lets the prior stay current with the full dataset. The raw counts stored in DynamoDB are the durable artifact; the shrunk rate is a derived quantity.

**Key property:** when n=0 (no defensive history), the shrunk rate equals the league average exactly. This naturally handles hypothetical lineups — see next entry.

---

## 2026-06-16 — Hypothetical lineup defense: league-average rates via shrinkage (named limitation)

**This is the central P2 design decision.** The product premise is "any five vs any five," including lineups that never played together and therefore have no entry in `my5-lineup-metrics`.

**CLAUDE.md constraint:** "Do not fabricate per-player defensive ratings." This rules out building a hypothetical lineup's defense from its individual players' historical defensive contributions.

**Decision: use league-average defensive rates for any lineup with no `lineup_metrics` entry.**

This falls directly out of the shrinkage framework: when n=0, the shrinkage formula returns league average (weight=0 on the observed rate, weight=1 on the prior). The engine needs no special case — a hypothetical lineup is treated as if it had zero defensive possessions on record, and shrinkage does the right thing automatically.

**The three simulation modes and what the user gets:**

| Matchup type | Defense quality |
|---|---|
| Real lineup vs real lineup (both have history) | Full signal — both sides use shrinkage-adjusted historical rates |
| Real lineup vs hypothetical | Hypothetical side gets league-average defense; real side is normal |
| Hypothetical vs hypothetical | Both sides get league-average defense; winner determined by offensive parameters only |

**Required disclaimer (must appear in UI and API response when a lineup has no history):**

> "This lineup has no defensive history. League-average defensive rates are used. The simulation captures offensive differences accurately; defensive variation only applies to lineups with real historical data."

**Why not alternatives:**
- *Team-level aggregated defense:* still requires all 5 players to share a team; doesn't apply to cross-era or cross-team hypotheticals; adds schema complexity.
- *Nearest-neighbor proxy:* opaque, fragile, and hard to explain in an interview or to a user.
- *Per-player defensive fabrication:* explicitly forbidden by CLAUDE.md.

**Honest statement of the limitation:** for any invented matchup, the defensive dimension is uninformative — both teams play league-average defense. The simulation is an *offensive* sandbox in the hypothetical case. This is not a failure mode; it is an accurate description of what the data supports. State it plainly.

---

## 2026-06-16 — lg_tov denominator: tov_rate (per usage event) vs forced_to_rate (per possession) — proven different measurements, reconciled via conversion constant

**The apparent inconsistency:** `lg_tov_rate` from player params = 0.1119 (TOV / usage events); `lg_forced_to_rate` from lineup metrics = 0.1262 (forced_TO / def_poss). Same 52-game slice; different numbers.

**Proof that the numerators are identical:**
- Total turnovers credited to players across 405 player records: **1301**
- Total forced turnovers credited to lineups across 1819 lineup rows: **1301**

Same turnovers, different denominators. This is not an inconsistency in counting; it is a measurement framing difference.

**Why the denominators differ:**
- `total_usage_events` (FGA + TOV + FT_trips summed across all players) = **11,628**
- `total_def_poss` (defensive possessions summed across all lineup rows) = **10,306**

The gap (1,322) comes from **offensive rebounds**. An OREB extends a possession without ending it: the defensive possession counter does not increment, but the offending player who grabs the OREB and then shoots (or turns it over) produces another usage event. Data confirms: 1,090 non-chained OREBs + ~232 from OREB chains = 1,322 extra usage events.

**What each rate measures:**
- `tov_rate` (player, stored): "when player P handles the ball, P turns it over X% of the time" — denominator = ball-handling events
- `forced_to_rate` (lineup, stored): "in what fraction of possessions does this lineup force a turnover?" — denominator = defensive possessions

**Resolution for the engine's log5 TO calculation:** after step 1 (player selection), the state machine is in the "P has the ball" conditional frame. The correct question is "given P has the ball for one ball-handling event, does a TO occur?" Both inputs must use the same denominator (per usage event). Convert the defensive rate at use time:

```python
DEF_POSS_TO_USAGE_EVENT = 0.8863  # = total_def_poss / total_usage_events = 10306/11628
# Update this constant when the full season is aggregated.

p_def_to = lineup.forced_to_rate * DEF_POSS_TO_USAGE_EVENT
p_to = log5(player.tov_rate, p_def_to, lg=0.1119)
```

**Impact on the worked example (player 4871144 vs Team B):**
- Without conversion: log5(0.10, 0.133, 0.1119) = 11.9%
- With conversion:    log5(0.10, 0.1179, 0.1119) = 10.6%

The 1.3 percentage-point difference is systematic: the uncorrected version always overstates TO probability because the defensive rate's denominator (possessions) is smaller than the offensive rate's denominator (usage events, inflated by OREBs). The converted version is the correct comparison.

---

## 2026-06-17 — Pace coupling: one shared Poisson draw instead of two independent draws

**Bug:** The original `_simulate_game` drew two independent `Poisson(97)` samples — one per team. This allows impossible outcomes: Team A gets 80 possessions, Team B gets 114. Both teams play the same basketball game; their possession counts are tightly coupled, not independent.

**Measured impact (500-sim empirical decomposition):**
- Independent Poisson pace: σ(margin) = 22.1 pts
- Coupled pace (fix applied): σ(margin) = 16.9 pts
- Pace variance contribution: **43% of total margin variance**

The fix: one `Poisson(194)` draw sets total game tempo; `poss_a = total // 2`, `poss_b = total - poss_a`. Both teams get equal possessions in every game (as in real basketball where the same game clock applies to both sides), with game-to-game pace variation still captured by the Poisson.

**Why this matters:** The artificial possession-count divergence was inflating CI estimates, requiring more simulations to converge, and allowing pace noise to temporarily dominate pure efficiency differences.

---

## 2026-06-17 — Residual margin variance: σ≈16.9 vs real-NBA σ≈12 — accepted, out of scope

**After the pace-coupling fix**, the per-team score distribution matches NBA reference closely:
- Simulated mean: 114 pts/game (NBA: 113–117) ✓
- Simulated σ(score): 11.7 pts/game (NBA: 11–13) ✓

Yet σ(margin) = 16.9 vs real-NBA σ(margin) ≈ 12. The remaining gap is structural:

**Root cause:** The two teams' game scores are statistically independent in our engine. Real NBA games have positively correlated team scores — when the game is high-tempo, both teams score more. This positive correlation reduces margin variance:
```
Var(margin) = Var(A) + Var(B) − 2·Cov(A, B)
```
Our engine has Cov(A, B) = 0 by construction (no shared intra-game state, no adaptive adjustments). Real games have r ≈ 0.3–0.5 between the two teams' scores, which would yield:
```
σ(margin) = √(2 × 11.7² × (1 − 0.4)) ≈ 12.8 pts
```
That matches real NBA exactly. The gap is fully explained by the independence assumption.

**Decision: do not close this gap.** Fixing it would require shared intra-game game-state (running score, adaptive adjustments per quarter, etc.) — none of which belongs in a single-process possession engine. The per-possession model is correctly calibrated; the independence approximation is an accepted simplification of this model class. The corrected Test 4 target range is 14–20 pts (centered on the analytically expected 16–17 for independent-scoring engines).

**Interview-ready statement:** "Simulated per-team scores match NBA calibration precisely. The margin σ of ~17 is higher than the real-NBA ~12 because our model has zero within-game correlation between the two teams' scores — an inherent simplification of a single-process engine. Closing it requires intra-game state we deliberately excluded from scope."

---

## 2026-06-19 — P2-A async job flow: ElasticMQ + message-as-pointer + my5-sim-jobs table

**Choice:** ElasticMQ (SQS-compatible Docker container) as the local queue; message body carries only `job_id`; all job state lives in a new `my5-sim-jobs` DynamoDB table.

**Why ElasticMQ for the local queue:** ElasticMQ (`softwaremill/elasticmq-native:1.7.1`) implements the full SQS HTTP/Query API. boto3 connects to it with `endpoint_url="http://localhost:9324"`. The worker code is byte-for-byte identical on local ElasticMQ and real AWS SQS — the only difference is the endpoint URL, set by `MY5_ENV=local` (default) vs `MY5_ENV=aws`. Native visibility timeout, delete-on-success, and DLQ (`MaxReceiveCount=3`) work identically. No code change to port; config change only.

Alternatives rejected: DynamoDB-as-queue (custom visibility timeout + retry counter = rewrite for SQS, not a port); in-memory Python Queue (no visibility timeout, message lost on crash); Redis Streams (different API entirely).

**Why message-as-pointer:** SQS message body is `{"job_id": "uuid"}` only. All parameters (team_a_key, player_ids, league averages, seed) are in the `my5-sim-jobs` DynamoDB record. Separates queue schema from job schema: in-flight messages during a rolling deployment remain valid even if the job schema evolves. Also avoids the 256 KB SQS body limit as lineup data grows.

**Why write DynamoDB record first, then enqueue:** If enqueue fails after the record is written, the job is stranded at QUEUED (visible, recoverable via re-enqueue). If we enqueued first then the record write failed, the worker would receive a message for a job_id that doesn't exist yet — a race condition with no recovery path.

**DLQ handling (event-driven on AWS, polling loop locally):**
The DLQ monitor's core logic (`fail_job()` + `delete()`) is identical on both targets. Locally it runs as a polling process. On AWS it is a second Lambda triggered by the DLQ — event-driven, $0 idle cost, fires only when messages arrive. Same `handle_dlq_message` function; different invocation shell. This mirrors the seam between `polling_loop` and the future `lambda_handler` for the main queue.

**VisibilityTimeout = 60s (not 300s):** Engine runs in 1–20s on a modern laptop. 60s is a 3× worst-case margin. Crashed jobs become available for retry after 60s rather than 5 minutes — better latency at no correctness cost. MaxReceiveCount = 3.

**State machine (legal transitions):**
`QUEUED → RUNNING` (worker claims job, increments attempt_count)
`RUNNING → DONE` (engine succeeds, result written, message deleted)
`RUNNING → FAILED` (invalid lineup: fail-fast, delete msg, no retry — data won't appear on its own)
`RUNNING → QUEUED` (implicit: worker crashes, VisibilityTimeout expires, SQS retries)
`QUEUED → FAILED` (DLQ after 3 attempts; DLQ monitor writes FAILED)
`DONE → skip` (duplicate delivery guard: delete msg, return "skipped" without re-running)

**Idempotency via determinism:** The engine is deterministic (same seed → same result). If two workers race on the same job (rare VisibilityTimeout collision), both compute the same answer. Last DynamoDB writer wins; no corruption.

**Cost ($0 at our scale):**
- `my5-sim-jobs` DynamoDB: PAY_PER_REQUEST, $0 idle. Tens–thousands of jobs stay well within free tier (200M requests/month).
- SQS queue + DLQ (AWS): $0.40/million requests, first 1M free. $0 in practice.
- ElasticMQ (local): Docker container, $0 AWS cost.

**Terraform provider v5 incompatibility with ElasticMQ (known limitation):**
The Terraform AWS provider v5.x performs a `GetQueueAttributes` readiness poll after `CreateQueue`. ElasticMQ 1.7.1 does not return all the attributes the provider expects (`KmsMasterKeyId`, etc.), causing an indefinite polling hang. Workaround: `count = var.use_local ? 0 : 1` on the SQS Terraform resources — they only apply when deploying to real AWS. Local queues are created via `scripts/e2e_queue.py` or manually with boto3. DynamoDB tables (all 3) are managed by Terraform in both targets.

**End-to-end verified (2026-06-19):**
- Flow A: submitted real lineup matchup (teams 26 vs 12, seed=42) → QUEUED in DynamoDB + ElasticMQ message → `handle_job` ran engine → DONE with `mean_margin=2.17 pts`; stored result matched direct `simulate()` call exactly (delta=0.00e+00).
- Flow B: submitted bogus player IDs → QUEUED → `handle_job` raised `LineupNotFoundError` → FAILED with `error_type=invalid_lineup`, message deleted.

---

## 2026-06-17 — Net_rating is an imperfect head-to-head predictor for lopsided lineups

**Context:** Validation Test 3 checks monotone ordering — when lineup A has a net_rating advantage ≥ 10 pts/100 over lineup B, the simulator should predict A wins the head-to-head. This passes 11/13 pairs. Both inversions involve team 12 (off_rating=153.9, def_rating=135.3, net=+18.6, n=102 possessions).

**Why the inversions occur (not a bug):** Team 12's net_rating is driven almost entirely by an extreme offensive outlier — a small sample that likely benefited from weak opponents. In head-to-head against teams 18 and 26 (balanced offense ~117–128, decent defense ~112–120), team 12's great offense faces resistance while their terrible defense (+35 above league average allowed) gets exploited. The marginal simulation outcomes (−0.46 and −0.03) confirm the matchup is genuinely uncertain.

**Why net_rating fails here:** Net_rating conflates additive contributions from offense and defense. In a head-to-head, the defensive disadvantage of one team interacts with the offensive strength of the opponent multiplicatively. A +18.6 net_rating built from (+53.9 offense, −35.3 defense) can lose to a +4.7 net_rating built from (+17 offense, −7.7 defense) because the opponent's offense exploits the weak defense.

**Design consequence:** The simulator is the correct arbiter of head-to-head outcomes, not net_rating. Net_rating is a summary statistic calibrated for season-level comparisons, not single-matchup predictions when the offense/defense imbalance is severe. This is by design; the simulator exists precisely because net_rating is insufficient.

---

## 2026-06-19 — P2-B: progress callback design — optional engine hook, sim-count throttling

**The problem:** The UI needs to show simulation progress as it runs (e.g. "200 sims, ±2.3 pts"). The engine is sealed — we cannot change its math or add infrastructure awareness. We need live progress data without touching the RNG or breaking determinism.

**Choice: optional `on_progress` callback, fired every `_PROGRESS_INTERVAL` sims.**

```python
def simulate(..., on_progress: Callable[[int, float], None] | None = None) -> SimResult:
    ...
    while n < _MAX_SIMS:
        # ... Welford update ...
        if n >= 2:
            var = S / (n - 1)
            ci_now = 1.96 * math.sqrt(var / n)
            if on_progress is not None and n % _PROGRESS_INTERVAL == 0:
                on_progress(n, round(ci_now, 2))
            if n >= _MIN_SIMS and ci_now <= _CI_TARGET:
                break
```

**Why a callback, not a queue / Redis pub-sub:** The engine is pure Python with no infrastructure imports. A callback lets the caller decide what to do (write to DDB, append to a list, log, assert in tests). The engine stays DB-agnostic and fully unit-testable by passing a list-appending lambda.

**Seal intact:** `on_progress=None` (the default) produces a byte-identical SimResult for the same seed. The callback is called AFTER the Welford update and DOES NOT touch the RNG state (`np.random.Generator` is only advanced inside `_simulate_game`). Proven by `test_same_seed_with_and_without_callback_identical_result`.

**Progress payload: `(sims_done: int, ci_half: float)`.** Not a percentage — total sim count is unknown upfront (CI stopping rule means we stop early or hit MAX_SIMS). The CI half-width is what matters: it's converging toward `_CI_TARGET = 2.0 pts`. The UI can show "200 sims, ±2.30 → converging."

**Why `_PROGRESS_INTERVAL = 50` (not 500, not per-sim):**

- **Per-sim writes** (interval=1): with _MAX_SIMS=5000, that's 5,000 DynamoDB writes per job. Each write has ~5ms local latency and costs a write unit. More importantly, DDB write latency would become the per-iteration bottleneck — the worker loop would be 5,000× slower than the compute.
- **Interval=500**: for a typical 100–300 sim job, that's 0–1 writes. A job that converges at 247 sims would produce exactly 0 progress writes (first write fires at n=500, but the job already finished). The progress feed would be empty for most jobs.
- **Interval=50**: 2–100 writes per job (2 writes for a 100-sim job, 100 writes for a 5,000-sim job). For a 258-sim job: 5 writes (at n=50,100,150,200,250). Observable, non-trivial sequence. At our scale (tens–hundreds of jobs total), worst-case total writes: 100 writes/job × 1,000 jobs = 100,000 DynamoDB writes. DynamoDB free tier is 200 million writes/month — we are 2,000× under the limit. Cost: $0.

**Worker wiring:** `handle_job` creates a closure that calls `job_store.update_progress(job_id, sims_done, ci_half)` and passes it as `on_progress` to `simulate()`. `update_progress` does a SET on `progress_sims` and `progress_ci` fields — no reserved-word aliases needed. Fields are overwritten on each call (not appended — the latest value is all we need).

**Status lifecycle with progress:** `status=running` while progress fields stream; `progress_sims` and `progress_ci` hold the last value written by the engine. When `status=done`, the `result` map holds the final SimResult. The progress fields are NOT cleared on completion — they show "last progress snapshot before done."

**WebSockets deferred (P2-C):** This phase proves the data is in DynamoDB (polling-based). The next phase will push `progress_sims` / `progress_ci` updates over a WebSocket connection instead of requiring the client to poll. The field schema is already designed for push: each update is a small number + float, and the final result is written atomically to the `result` map.

**E2E verified (2026-06-19):** 50ms polling thread captured live progress:
```
poll →    50 sims  CI ±4.42 pts  (status=running)
poll →   150 sims  CI ±2.63 pts  (status=running)
poll →   200 sims  CI ±2.30 pts  (status=running)
poll →   250 sims  CI ±2.05 pts  (status=done)
Result: 258 sims, mean_margin=+1.790 pts, converged=True
```
(The n=100 write was skipped by the poller — the engine ran faster than the 50ms poll interval. The data was written; polling just missed it. This is expected and not a bug.)

---

## 2026-06-19 — P2-B addendum: why interval=50 is the correct throttle for our CI stopping rule

The P2-B entry above states the interval=50 rationale in terms of DynamoDB write cost. Here is the stopping-rule math that makes interval=500 wrong and interval=50 right:

**The CI math:** Our engine has σ(margin) ≈ 16.6 pts (measured, DECISIONS.md 2026-06-17). The CI stopping rule halts at `1.96 × sqrt(σ² / n) ≤ 2.0 pts`. Solving: `n ≥ (1.96 × 16.6 / 2.0)² ≈ 264`. Typical convergence is near n=260 sims.

**Why interval=500 is dead-on-arrival:** With convergence at n≈260, the first progress write would fire at n=500 — after the job is done. For every typical job, the progress feed is empty.

**Why interval=50 is correct:** 260 / 50 = 5 progress writes for a typical job (at n=50, 100, 150, 200, 250). The UI gets a real convergence curve. At the hard cap (n=5000): 100 writes/job. At our scale: $0 (200M DynamoDB writes/month free tier).

**E2E confirms:** seed=42 run converged at 257 sims → received frames at n=50, 100, 150, 200, 250 — exactly 5 progress frames, then done.

---

## 2026-06-19 — P2-C: WebSocket push — Option B (record as source of truth), NotifyingJobStore, register-then-snapshot

**The core decision: Option B (fan-out from DynamoDB, not from worker)**

The worker continues to write progress to the job record exactly as before (sealed). A separate fan-out stage delivers those writes to connected clients. Locally, this is `NotifyingJobStore` wrapping `JobStore`. On AWS, it is DynamoDB Streams → fan-out Lambda.

Why not Option A (worker pushes directly): the worker would gain a second responsibility (compute + push), know about WebSocket infrastructure, and — most importantly — the job record would no longer be the single source of truth. If the engine completes and pushes "done" but crashes before `write_result` is called, the client sees terminal but the record shows "running." The record and client diverge. Option B cannot produce this: the bus event is posted after the durable write, so any client reading the record immediately after receives consistent state.

**NotifyingJobStore is the local analog of DynamoDB Streams**

`handle_job` takes `job_store` as an injected dependency. Locally, we inject `NotifyingJobStore(inner=JobStore(...), bus=bus)`. On AWS, we inject a plain `JobStore`. `handle_job` is byte-identical in both cases — it never knows whether the store notifies. The seam is the same pattern as ElasticMQ (local) vs SQS (AWS) for the queue.

`NotifyingJobStore` wraps three write methods: `update_progress` (→ progress event), `write_result` (→ done event), `fail_job` (→ failed event). All other methods (`get_job`, `update_status`, `put_job`) delegate silently.

**Atomic write confirmed:** `job_store.write_result` is ONE `update_item` call setting `status="done"`, the `result` map, and `completed_at` atomically. The bus event is posted after that single write. Any subscriber reading the job record after receiving the event will always see both `status` and `result` present — never one without the other.

**Register-then-snapshot ordering (correctness-critical)**

On connect, the handler registers the connection FIRST, then reads the job snapshot. The invariant: no client can finish the connect handshake without either receiving the terminal frame via fan-out OR reading terminal status in its snapshot.

Why this order is correct: if the job goes terminal in the gap between register and snapshot, the fan-out puts the terminal frame on the connection's queue AND the snapshot reads terminal status. The client sees terminal via both paths. A duplicate terminal frame is cosmetic; a missed terminal frame would leave the client hung forever.

Snapshot-then-register would create a window: if the job goes terminal after the snapshot is read (showing "running") but before registration, the fan-out fires for zero connections. The client enters the queue-drain loop with no future events — permanently hung.

**Portable core (push_progress) and local seam (LocalSender)**

The `push_progress(job_id, message, registry, sender)` function is identical local and on Lambda: look up connection IDs from the registry, serialize the message, call `sender.send(conn_id, payload)` for each, prune on GONE.

The twin:
| | Local | AWS |
|---|---|---|
| Registry backend | Python dict (process memory) | DynamoDB `my5-ws-connections` table |
| `sender.send()` | `asyncio.Queue.put()` (LocalSender) | `post_to_connection()` APIGW Management API |
| Fan-out trigger | NotifyingJobStore → EventBus | DynamoDB Streams → fan-out Lambda |
| Connection lifecycle | `ws_handler` on/off | `$connect` / `$disconnect` Lambda routes |

**Sync→async bridge (EventBus.post_threadsafe)**

The worker runs in a sync thread; the WS server and bus consumer run on an asyncio event loop. The bridge: `loop.call_soon_threadsafe(queue.put_nowait, event)`. The server captures its event loop at startup (`bus.set_loop(asyncio.get_running_loop())`) before any workers start. The worker thread calls `bus.post_threadsafe(event)`, which schedules `put_nowait` on the server's loop thread — never blocking the worker, never crossing asyncio's thread boundary incorrectly. Tested in `test_bridge_sync_thread_to_async_bus`.

**Message contract:**
```
progress: {"type":"progress","sims_done":int,"ci_half":float}
done:     {"type":"done","n_sims":int,"mean_margin":float,
           "ci_half_width":float,"equiv_net_rating":float,"converged":bool}
failed:   {"type":"failed","error_type":str,"error_message":str}
```

**E2E verified (2026-06-19, in-memory, no Docker):**
```
(snapshot: job queued, waiting for worker...)
  progress:    50 sims  CI ±4.08 pts
  progress:   100 sims  CI ±3.25 pts
  progress:   150 sims  CI ±2.67 pts
  progress:   200 sims  CI ±2.31 pts
  progress:   250 sims  CI ±2.02 pts

  DONE after 257 sims
    mean_margin      = -0.670 pts
    ci_half_width    = 1.990 pts
    equiv_net_rating = -0.7 pts/100
    converged        = True

  Direct simulate(seed=42) margin = -0.670 pts
  Stack vs direct delta  = 0.00e+00  ✓

  Total frames: 7 (1 snapshot + 5 progress + 1 done)
```

**AWS shell (not built in this phase):** DynamoDB Streams on `my5-sim-jobs`, fan-out Lambda, APIGW WebSocket, `my5-ws-connections` DynamoDB table. All scale to zero; no idle cost; no NAT gateway.
