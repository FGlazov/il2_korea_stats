# 14 — Ingest Internals (parser, catalog, ingest jobs, persistence)

How iteration 1 actually parses, catalogs, discovers, archives and stores missions. The game rules (fate, outcome, credit) are in
[13_game_rules.md](13_game_rules.md). Written from the iteration 1 implementation decisions the maintainer reviewed on 2026-10-03
(formerly `OQ-I1-*` in doc 11). Everything here is `[DECIDED]` unless tagged otherwise. Code locations are given once per item.

## Contracts between layers

- **Events** are frozen dataclasses in `core/logparse/events.py`, one per AType. Unmapped keys go to `extra`; unused or unknown ATypes become a
  `GenericEvent`. **Replay output** is `core/replay/result.py` (`MissionResult` with `MissionInfo`, `SortieResult`, `KillResult`), with all
  times in ticks; `ingest` turns ticks into UTC datetimes. (Maintainer: keep as a technical decision, revisit if it causes issues.)
- `ReplayRules` (`core/replay/config.py`) holds every rule threshold; the `[replay]` config section sets any field by name.

## Parser (`core.logparse`, TD-20)

**Files and missions** (`files.group_mission_files`)
- Mission UID = the timestamp in `missionReport(YYYY-MM-DD_HH-MM-SS)[N].txt[.zip]`, as a string. Parts sort numerically by N. Other file names
  (`*.weather.json`, ...) are ignored.
- A plain `[0].txt` can be a raw part or a whole-mission archive (same name). The caller decides: production reads raw parts, `ingest --from`
  reads archives. A `[N].txt` with N ≥ 1 always means raw parts. `.txt.zip` is always an archive.
- Several inputs for one mission UID: raw parts beat any archive (newer); a `.txt` archive beats a `.txt.zip` (same content).
- A zip must hold exactly one `.txt`. With zero or several we can't tell which is the mission, so the whole mission is marked failed (a file
  problem, not a line problem).
- Encoding: UTF-8 with `errors="replace"`, leading BOM dropped, line endings normalized. Blank lines are skipped and not counted.

**Lines** (`parser.py`)
- Generic `KEY:value` tokenizer. Free-text values (`NAME`, `TYPE`, `SKIN`, `MFile`) are anchored on the **next known key** of that AType; a nickname
  containing e.g. ` TYPE:` would be cut there (never seen in 18M lines). `KEY: value` (space after the colon) takes the next word unless it is
  itself a key.
- **Bad line** = malformed token, missing required key, unconvertible value, duplicate key, or unbalanced parentheses. Counted in `lines_bad`,
  never fatal.
- **Unknown ATypes never fail**; the unparsed text is kept in `GenericEvent.fields["#raw"]`. ATypes 27/28 are documented and deliberately
  dropped, so they count as **ignored**, not unknown; 17, 22, 23, 29 and anything new count as **unknown** because their appearance is news.
- **Unknown keys** go to `extra` and are counted as `"<atype>:<KEY>"`, except documented trailing keys (`MID` on 12, `ROUNDS`/`POINTS` on 0,
  `TARGETS()`... on 8, `IDS()` on 9).
- **Warnings are limited per kind** (maintainer request, 2026-10-03). Each problem has a kind (`parser.WarningKind`: malformed line or
  token, unbalanced parentheses, duplicate key, missing key, bad value, unknown AType, unknown key, log version change), carried by
  `ParseError.kind`, so no string matching. The first warning of a kind, and of each new unknown `"<atype>:<KEY>"` or AType, is always kept;
  after that at most 20 per kind and 200 per mission; at the end one "N more '<kind>' warnings suppressed" line per kind. A flood of one problem
  can't hide a new one. Unknown keys and ATypes warn on first sight (ignored ATypes 27/28 don't). Each warning quotes at most 200 chars.
- `stats.log_version` = the first AType 15 `VER`; a different later `VER` is a warning.
- **Fast path** (2026-10-04, `[PROPOSED]`): one compiled regex per AType matches the whole token part of a line; an exact match skips the Python
  tokenizer, anything else takes the generic tokenizer, which stays the definition of the semantics and the errors. A test compares both routes
  on the fixtures and on mutated lines. Speed: see "Speed" below (before the fast path: 210 sample missions parsed in 155 s, ~8 MB/s).
- **Direct event builders** (2026-10-04, speed round 2, `[PROPOSED]`): for the ATypes that make up 98% of a log (hits, damage, kills, object
  declarations, gun bursts) the fast path's regex groups feed the event constructor directly, without the token dictionary. A value that does not
  convert raises `ValueError` and hands the line to the generic builder, which stays the definition of the exact error. The test that compares the
  routes covers the direct builders too.
- **Explosion bursts** (2026-10-04, speed round 3, `[PROPOSED]`): `AType:1 ... AMMO:explosion` lines are 72% of all lines, and 99.96% have a player-owned
  attacker, so they cannot be dropped. `parse_lines` merges consecutive ones with the same tick and attacker into one
  `ExplosionBurstEvent(tick, attacker_id, target_ids)`, found with plain string operations (`startswith` against the open burst's prefix, no regex,
  one event per burst). Only lines of exactly `T:<tick> AType:1 AMMO:explosion AID:<n> TID:<n>` (single spaces, ASCII digits, nothing around) take part;
  any other line, however close, goes through `_parse` unchanged and ends the burst, so warnings, `lines_total` and `lines_bad` are the same. The open
  burst is flushed before any other event and at the end of the line iterable (a live batch never holds one back; a burst spanning two batches gives two
  bursts, which `Replay` merges because a detonation is keyed by tick). `Replay._on_explosion_burst` checks the attacker once and loops over the
  targets. `parse_line` / `_parse` still return a `HitEvent`. Tests: `tests/unit/logparse/test_explosion_burst.py` (bursts expanded == per-line
  parsing == generic tokenizer, stats and warnings equal, mutated inputs, random batch splits, replay results and detonations equal).
- **Chunked line splitting** (`files._decode_lines`): files are read in 1 MB chunks, decoded incrementally (`errors="replace"`) and split with C-level
  string methods; `\n`, `\r\n` and `\r` end a line, nothing else does (not `str.splitlines`), and a `\r\n` split by a chunk boundary is rejoined.
  The result is the same lines as `TextIOWrapper(newline=None)` gave, with less per-line Python.

## Catalog (`core.catalog`, TD-24)

- Data ships in `core/catalog/data/`: `objects.csv` (log name, display name, class, playable), `payloads.csv`, `payload_aliases.csv`
  (`F-86A-5` → `f-86a`). Duplicate names (case-insensitive) are a load error.
- **Name lookup** (`canonical_type_name`) strips static block suffixes `[group,index]` (`GAZ_63[64606,0]` → `GAZ_63`) and parachute suffixes
  (`CParachute_<n>`), ignores case, and keeps the original case as `log_name`. An unknown type's display name is that canonical name.
- **Classes**: `fighter`, `attacker`, `bomber`, `transport`, `gunner`, `tank`, `vehicle`, `aaa`, `ship`, `static`, `ordnance`, **`crew`**
  (crew bots, B-29 crew groups) and **`equipment`** (parachutes, ejection seats, spotters, vehicle turrets and rangefinders). `crew` and
  `equipment` were added 2026-10-03 at the maintainer's request; before that these rows were `unknown` with `is_known = true`.
  `unknown` now means "not in the data" only.
- `Turret_*` / `Multiturret_*` are `gunner`; only `Turret_IL10` is playable. **No bomber is playable** in IL-2 Korea today: bombers (B-29, Tu-2)
  are AI only; a bomber expansion is announced (maintainer, 2026-10-03), so `is_playable` is data, never hard-coded.
- **Static copies** of vehicles and ships (`Parked <aircraft>`, `Box Car A`, `GAZ_63`, ...) are `static`, AI-driven ones (`GAZ-63`) are `vehicle`.
  Both count as ground kills (most server vehicles are static for performance; maintainer, 2026-10-03). Scoring can weight classes later.
- **Propulsion** (2026-10-03, for the Elo pools): `prop` / `jet` for every aircraft row, blank for everything else (a test checks both). Jets:
  F-80C-10, F-84E, F-86A-5, MiG-15bis. Props: F-51D, La-11, Yak-9P, IL-10 and the AI types (B-29, Tu-2, C-47B, Li-2).
- Heavy artillery is `vehicle`, self-propelled guns are `tank`, AA vehicles and AA platform cars are `aaa`: best guesses, admin-editable (TD-24).
- **Payloads**: 99.3% of sample spawns resolve. Missing payloads (new ones arrive with game updates) must never break a page: the sortie shows the
  raw payload ID (OQ-25 tracks extracting the rest). `payload_aliases.csv` becomes admin-overridable together with object names (it2, TD-24).
  **Rules never read the payload name**: the combat role uses the AType 10 ammo counts, which exist for every spawn (doc 13). They also show
  that F-51D payloads 54–58 in `payloads.csv` were shifted by one row: the editor list lacked `M29CLUS-2 + ATAR-6`; fixed 2026-10-03, ids
  0–63 are now contiguous and a test keeps them so.

## Ingest jobs

**Config** (`config.py`, TD-11)
- Sources, later wins: defaults → `il2ks.toml` → `IL2KS_<SECTION>_<KEY>` env vars. File lookup: `--config`, `IL2KS_CONFIG`, `./il2ks.toml`,
  `<data dir>/il2ks.toml`. Relative paths resolve against the file's folder.
- A fully commented `il2ks.toml` template with every key and default ships in the package (for `il2ks setup`); a test keeps it equal to the
  code defaults.
- **Server timezone** (TD-15): `[server] timezone` (optional, default unset) → `TZ` env → `/etc/localtime` → UTC. Windows has no IANA name, so
  Windows admins set it (or run DServer on UTC); `il2ks doctor` should warn.
- **Server UID** (TD-17): `[server] uid`, else `<data dir>/server_uid.txt` (generated once).
- **Ratings** (FR-WEB-19): `[ratings] start` (1500), `k` (32), `cross_pool_weight` (2.0). First guesses, to tune once real ratings exist
  (maintainer, 2026-10-03); a change takes effect with `il2ks rebuild-aggregates`, no reprocess needed.
- **Other level-2 rule sections**, also applied with `il2ks rebuild-aggregates`: `[score]` (points, `penalty_*_pct`, flat penalties, leaderboard
  minimums; doc 13), `[marks]` (stat highlights), `[killboard] assists`. `[rules]` (`credit_rams`, the ram thresholds, doc 13) changes kills and
  deaths themselves, so it needs `il2ks reprocess --all` (or `--since`). Replaced or removed keys (`[score] penalty_*`, `[rules] parachute_deaths`) are
  ignored with a warning (`Config.warnings`: logged on load and shown by `il2ks doctor`).

**Discovery and completeness** (`discover.py`, FR-ING-2/16/18/19)
- Order: in remote mode every part must be unmodified for `stable_seconds` (60); then complete if a newer mission's `[0]` exists, or no part was
  written for `idle_minutes` (10), or AType 7 was seen (last 3 parts) **and** nothing was written for `settle_seconds`. Whole-mission archives are
  complete at once.
- `settle_seconds` = **300** (maintainer, 2026-10-03: 60 s is too low; il2_stats waited 120 s after the newest part, doc 12). Cleanup lines
  follow AType 7, and since a newer mission's `[0]` completes a mission anyway, this only delays the last mission before a server stop.
  When several reasons apply, `newer_mission` beats `idle` beats `mission_end`.
- Each run records **why** the mission counted as complete (`IngestRun.completion_reason`: `mission_end`, `newer_mission`, `idle`, `import`,
  `reprocess`), so admins see missions completed only by the idle timeout (FR-ING-18). `Mission.completed_cleanly` = AType 7 seen.
- Fingerprint = sha256 over (name, size, mtime) of the mission's files. A changed fingerprint re-ingests the mission.
- Retries (FR-ING-19): attempt 1 fails → retry in 5 min, then 30 min, then 2 h, then stop. A new fingerprint or il2ks version resets the count.
  Skipped decisions are logged, not stored as `IngestRun` rows.

**Archive** (`archive.py`, FR-ING-8)
- `<data dir>/archive/YYYY/MM/missionReport(<uid>)[0].txt.zip`, one entry holding all parts concatenated (the same shape as the sample data, so
  it imports like any archive). Verified (CRC + content sha256) in a temp file, then renamed into place. `IngestRun` stores the relative path and
  the zip's sha256.
- Late parts after the originals were moved: new archive = previous archive + new parts.
- Originals are moved (default) only **after** the DB commit. `ingest --from` never touches its source.
- Reconcile: an ingested mission whose originals are still in the log folder but whose archive is missing is re-ingested from the originals.

**Single writer** (`lock.py`, FR-ING-20): an OS file lock on `<data dir>/writer.lock` (the OS releases it when the holder dies, so no stale locks).
A second writer exits with code 3 naming the holder, or waits with `--wait`. `watch` takes the lock per tick, so `reprocess` can run while
`watch` is up.

**Runner, reprocess, CLI**
- One `IngestRun` per attempt that did work; the OK run commits in the same transaction as the mission. Parse reads the archive, not the originals.
- Every writer command applies pending migrations first (FR-OPS-3).
- Exit codes: 0 ok, 1 some missions failed, 2 usage/config error, 3 lock held.
- **`il2ks reprocess` needs an explicit selection** (2026-10-03): `--all`, `--mission`, or `--since/--until`; bare `reprocess` prints help
  and exits 2, so nobody reprocesses a year of logs by accident. The admin's ingestion status page has a **"Reprocess all missions"**
  button (with confirmation, `add_reprocessrequest` permission): it files a `ReprocessRequest` row (one pending at a time) that the `watch`
  loop (also under `il2ks run`) runs at its next tick under the writer lock, updating progress and the result on the status page; a busy
  lock leaves it pending, a crash marks it failed, a request left running by a dead process is marked failed at the next watch start. New
  missions wait while it runs.
- `il2ks reprocess` takes the missions from their newest archive-writing run plus archives without any run (rebuilding a lost DB). Filters:
  `--mission UID` (repeatable) and **`--since DATE` / `--until DATE`** (inclusive, by the mission UID's server-local date; maintainer use case:
  "scoring changed, reprocess the last 6 months"). Parse and replay run in low-priority worker processes; the main process is the only writer.
  A reprocess failure leaves the previous rows in place and never schedules a retry.
- `setup`, `web`, `run`, `doctor`, `createadmin`, `backup` and `restore` are built (doc 16). Tools under `il2ks dev` (`check`, `bench-ingest`, `dump-db`, `bump-templates`, `translations`, `assets`, ...) are for contributors (doc 08).

**Log files** (`logsetup.py`, TD-27): JSON lines with `time` (UTC), `level`, `logger`, `message`, `process`, **`server_uid`**, extras,
exception. One file per process and UTC day, `<process>-YYYY-MM-DD.log`, kept for `log_keep_days` (default 14) instead of 10 MB × 5 (maintainer,
2026-10-03). Files are never renamed, so several processes on Windows can't trip over each other.

**Dead code** (TD-12): `uv run vulture` at confidence 60 over `src/` (Django models, migrations and settings excluded). Intentional leftovers are
listed with a reason in `vulture_whitelist.py`: `parse_line` (public single-line API), `Replay.snapshot` (FR-ING-15), parsed-but-unused event
fields (groups, store and rocket IDs: later squadron and ordnance stats), and framework hooks.

## Persistence (`ingest/persist.py`, `ingest/aggregates.py`, TD-08)

- **Counted sorties = pilot sorties.** Gunner sorties are stored (`role = gunner`) but feed no counters until gunner stats exist (FR-WEB-14).
  `PlayerMission` rows exist only for players who flew a pilot sortie in the mission; `Mission.players_total` still counts everyone.
- **One counter registry** (`ingest/counters.py`): the list of counters is defined once and used by `PlayerMission`, `Player` and
  `PlayerAircraft`; a test checks it matches the model.
- **Save order** (`persist.save_mission`, one transaction, `[PROPOSED]`): the tour (`ensure_tour`), the `Mission` row, game objects and countries,
  `Player` rows, the sorties (each gets its air and ground score from `ingest.scoring.apply_score` under the `[score]` rules, as it is written),
  `SortieGunHits` (the gun hit lines per sortie and ammo), the PvP `Kill` rows, `PlayerMission`, the mission's own counters, `MissionAircraftAmmo` and `MissionAircraftAmmoMix` (the ammo and ammo mix of each destroyed aircraft, with that aircraft's own combat role and weapon mods, migration 0057). Then level 2, always after the rows it reads. **Level-2 refresh** (maintainer, 2026-10-05: every level-2 refresh is a full refresh of the touched tours; no per-entity tracking, `[PROPOSED]` details):
  `save_level1` returns only the ids of the tours it touched (the mission's tour and, when a re-ingest moved it, the old one; the `Touched` set is gone), and
  `aggregates.refresh_tours(tour_ids)` is the one level-2 path for the single-mission save, the batches, the live passes and `discard_provisional_mission`
  (`rebuild_aggregates` calls it with `None`: every tour and everything that has none). It finds what to recompute by queries over the tours' level-1 rows
  and the rows level 2 already holds for them (so an entity that dropped out of a re-ingested mission is corrected too): the players with a sortie,
  `PlayerMission` or `PlayerTour` row in the tours, the types flown in them (counted sorties, `PlayerTourAircraft`, `TourAircraftStats`), the (killer type, victim type)
  pairs of their counted kills (and the tours' `AircraftMatchup` rows), the types that have ammo rows (they are not per tour), the UTC days of their missions. It then runs
  two steps (`_recompute_tour_scope`, then `_recompute_all_time`) with the existing recompute functions:
  1. `recompute_players` for those players, limited to the tours: totals, `PlayerAircraft`, the prop/jet pools
     (`PlayerPool` / `PlayerTourPool`), `PlayerTour` / `PlayerTourAircraft`, the favourite loadout rows (`PlayerAircraftBuild`: the payload only since OQ-117; `ingest.builds`, from the sorties), identity and names, then the killboard rows (`PlayerKillboard` /
     `PlayerTourKillboard`, `ingest.pairs`; `PlayerTypeKillboard`, `ingest.type_board`), the streaks (`PlayerStreak` / `PlayerBestStreak`,
     `ingest.streaks`) and the medals (`PlayerAchievement`, `ingest.achievements`, doc 17; all time and per tour);
  2. `recompute_aircraft_stats` for the types (`AircraftStats`; `TourAircraftStats` per tour, role and modification pattern for the tours; `AircraftPayload` and `AircraftMods`; `PlayerAircraftScope`, the top-pilot rows of every scope; reads the players' `PlayerAircraft` rows, so it comes
     after step 1) and `recompute_matchups` (`AircraftMatchup`: for each type pair the scopes: all time and per tour, all kills
     and intercept kills only, and, since 0057, the role / modification scopes of the killer's and of the victim's sortie, `scoped_side`);
  3. `_recompute_all_time`: `recompute_aircraft_ammo` (`AircraftAmmoStats` and `AircraftAmmoMixStats`, the sums of the two mission tables per tour, role and modification pattern) and `recompute_days` (`ActivityDay`).
  The all-time player and type rows are still written by the same recompute functions in step 1 and 2 (they read all of a player's level-1 rows); `_recompute_all_time` is the
  seam where "all time = the sum of the tour rows" will go. Known limit: the old day of a re-ingested mission whose start time moved is found only by `rebuild-aggregates`.
  The callers then add, once per call: `recompute_holders` (`AchievementHolders` per scope, after the medal rows; counted in the database with `GROUP BY`),
  `recompute_payload_elo` when no ratings follow, then
  4. `recompute_thresholds` (`StatThreshold`, the touched tours and all time, one population per metric and board minimum, loading only the rows that reach at least one metric's minimum; skipped by `reprocess`, which recomputes once at the end);
  5. `recompute_ratings` (Elo per pool and per type, all kills replayed; then `recompute_payload_elo`, the average Elo of the pilots of each loadout and mod set, `elo_avg`; same skip);
  6. `bump_data_version` (TD-28).
- **Provisional sorties of the running mission** (FR-ING-15, built 2026-10-04, `ingest/live.py`; all `[PROPOSED]` except the feature itself): the
  `LiveTracker` that feeds "online now" also keeps a `LiveReplay` of the running mission and, every `[live] sorties_interval_s` (default 120 s),
  saves it as a real `Mission` with `is_live = true` through `persist.save_mission`, upserting by the **same natural keys** as the final save, so
  URLs stay the same until the mission ends. Level 1 is written every pass; level 2 (profiles, boards, aircraft pages) is recomputed for what was
  touched every `[live] aggregates_interval_s` (default 300 s; 0 = only at the final save). **Elo ratings and stat thresholds wait for the final
  save** (they depend on mission order; `ratings._games` skips live kills). The final save is the ordinary ingest of the complete mission: it
  rewrites those rows and clears `is_live`. A mission whose files vanish without being ingested is deleted again, and so is every provisional mission when
  the admin turns `SiteSettings.show_live_sorties` off (`discard_provisional_mission`). A provisional save takes the writer lock **without waiting**
  (a held lock skips that pass), and bumps the data version in its own transaction; the `Live*` tables themselves still take no lock and never bump it.
  `rebuild-aggregates` runs the same functions for everything, after re-scoring the sorties, and also stores `[killboard] assists` in
  `SiteSettings`.
- **Level-2 updates = recompute the affected players from level 1** (maintainer, 2026-10-03; replaces "subtract old, add new"). After a
  mission's level-1 rows are written, every player who had or has a sortie in it gets their totals, per-aircraft rows and identity fields
  recomputed from their level-1 rows. `rebuild-aggregates` uses the same code for all players. Why: no delta arithmetic to get wrong, and a
  re-ingest can't drift from a rebuild. Cost grows with a player's history; in it2 the recompute is bounded to the mission's tour plus the
  all-time row.
- **Tour-level refresh, all time = sum of the tours** (maintainer, 2026-10-05, restating an earlier requirement that was not recorded
  here): to bound complexity as the history grows, level 2 is refreshed per tour, and the all-time stats are built on top of the sum of
  the tour stats rather than by re-reading a player's whole level-1 history. Batches (20+ missions) track only the tours they touched and
  fully refresh those tours; every other refresh (a single mission, a live pass, a rebuild) works the same way: one path, a full refresh
  of the touched tours, no per-entity tracking (maintainer, 2026-10-05: "not just for batched refreshes, but all of them"). **Not built yet:** today the all-time rows are still recomputed from level 1 (see above); the inventory of what
  sums cleanly (counters, min/max, weighted averages) and what does not (the Elo replay, streaks across a tour boundary, distinct counts,
  population thresholds) is in progress (roadmap).
- **Elo ratings** are the one level-2 value that isn't per player: they depend on the order of every qualifying kill, so
  `ingest/ratings.py::recompute_ratings` replays all of them (ordered by mission start, kill time, row id) through the pure
  `core/ratings/elo.py` and writes only the players whose rating changed (the per-pool ratings on `Player`, and the per-type ratings on `PlayerAircraft`, doc 13). It runs after each mission save (same transaction), and once at the
  end of `rebuild-aggregates` and `reprocess`. So a mission imported late lands in the right place in the order. Cost: about 0.02 s per mission
  at sample scale, growing with the total number of kills; if it ever matters, replay only from the earliest affected mission onward.
- **Batched level 2 for long runs** (`ingest/batch.py`, roadmap item, 2026-10-05, `[PROPOSED]`). Maintainer, 2026-10-05: batches track only the touched tours and fully refresh them (no per-entity tracking), to bound complexity. A run with `BATCH_MIN = 20` or more missions to do
  (`ingest` after its classify pass, `reprocess` after choosing its targets) saves **level 1 only** per mission (`Pipeline.save_level1` =
  `persist.save_level1` + `bump_data_version`; still one transaction per mission, the `IngestRun` row in it) and does level 2 later. Fewer than 20:
  nothing changes (`Pipeline.save` = `save_mission`: level 2, ratings and thresholds per mission). A pipeline without `save_level1` (test fakes) never batches.
  - **ingest**: a `Level2Batch` tracks **only the ids of the tours** its saves touched (`add(tour_ids)`, called inside the save's transaction so a rolled-back mission adds nothing). After each mission
    (saved or failed: progress counts missions) `mission_done()` applies `persist.apply_level2(pending_tours, payload_elo=False, holders=False)` (= `refresh_tours`) and
    `bump_data_version` in its own transaction whenever `done * 10 // total` crossed the next line (9 passes at most; the last 10% is the end's). At the end,
    in one transaction: the same pending tours, then `recompute_ratings` once (it replays every kill ordered by mission start, so "in mission order" holds
    whatever order the missions were saved in), `recompute_holders`, `recompute_thresholds` for every tour the batch touched: what `save_mission` does per mission,
    in the same order (`persist.apply_batch_end`). Between passes the pages show the new level-1 rows (missions, sorties) with level 2 at most 10% behind;
    the new loadouts' average Elo and the holder counts wait for the end.
  - **reprocess** (`defer_ratings` pipelines, also the admin's request and `watch`): level 1 only per mission and **no 10% passes**: the final
    `rebuild_aggregates` (unchanged: it also re-scores sorties and applies a changed `[score]`, `[tours]` or `[killboard]` config) recomputes every
    level-2 table anyway, so per-mission or 10% passes were pure waste (they used to run per mission and the rebuild redid all of it). End state identical.
  - **Crash safety**: the loop is in a `try/finally`. Ctrl-C, an exception or a failing pass in the middle still applies what is pending (ingest: `Level2Batch.finish`;
    reprocess: the rebuild), so level 2 matches the saved missions. A pass failing inside that `finally` is logged ("run `il2ks rebuild-aggregates`"), not raised over the first error.
    A **hard kill** (power cut, `kill -9`, OOM) loses the in-memory pending tours (the marker keeps them, below): level 1 of the saved missions is committed, level 2 (and the Elo
    ratings) lag by at most the last 10% (reprocess: by every mission so far), and a re-run skips the missions as `unchanged`.
  - **Pending marker** `[PROPOSED]` (2026-10-05, makes the hard kill detectable and self-healing): `SiteSettings.level2_pending` (JSON, empty = none; migration
    0059, on the settings row every page reads anyway) is set to `{"since", "command", "pid"}` when a batch starts, and gets `"tours": [ids]` (every tour the batch's saves touched so far, one small update only when the batch reaches a new tour, in the save's transaction) (`Level2Batch.start()` before the first save; a batched
    `reprocess` sets it too) and cleared in the same transaction as the last level-2 pass (`finish`), by any `rebuild_aggregates` (so the reprocess's final rebuild
    and `il2ks rebuild-aggregates` clear it) and by a batch that saved nothing. The marker is **not** cleared by the 10% passes. At the start of every `ingest`,
    `watch` and `reprocess` run, writer lock held, `batch.repair_pending` finds it still set (a killed process; the lock proves nobody is running). With `tours` in it, it runs `refresh_tours(those)` and then ratings, holders and thresholds
    (`persist.finish_batch`); without (a batched reprocess, an older marker) it runs `rebuild_aggregates` first. It logs a warning with the start time and pid, then clears the marker. `il2ks doctor` (ingestion check) warns "Level 2 ... may be behind" with the fix
    `il2ks rebuild-aggregates` while it is set. An ordinary exception or Ctrl-C leaves no marker (the `finally` finishes the batch). Cost: two one-row updates per batched run
    and one read per run. Tests: `test_a_killed_batch_is_detected_by_doctor_and_repaired_by_the_next_run`, `test_a_killed_batched_reprocess_...`, `test_a_finished_batch_leaves_no_marker`.
    Tests: `test_the_marker_names_the_tours_of_the_batch`, `test_a_marker_without_tours_is_repaired_by_a_full_rebuild`. A killed batched reprocess still repairs by a full rebuild.
  - **Equality** is tested table by table (`tests/integration/test_batched_level2.py`, `tests/db_canon.py`): batched ingest == per-mission ingest == after a rebuild,
    and batched reprocess == per-mission reprocess; plus a batch that re-ingests missions that moved tours (both tours refreshed) and a batch over several tours.
  - **Measured** (2026-10-05, `bench-ingest` on a copy of every 5th sample, 42 missions, machine loaded by other jobs, so wall clock and CPU swing 2x):
    wall 88.4 s and 84.1 s per-mission path (`BATCH_MIN` forced high) against 65.7 s and 62.6 s batched, about 26% faster (level 2 plus ratings 22-25% of
    the run before, 18-19% after; 11.7-13.6 s of it in the batched passes). First, quiet CPU-time run: 9.6 s before (level 2 2.8 s) against 6.5 s after (1.4 s).
    The saving grows with the batch: a player in many missions is recomputed once per pass instead of once per mission.
  - **Measured with the tour-based refresh** (2026-10-05, same 42 missions, one monthly tour, `bench-ingest`, wall clock on a loaded machine): per-mission path 68.7 s with the previous
    Touched-set version (level 2 18.2 s, median 350 ms per mission) -> 115.3 s with the full-tour refresh (level 2 51.1 s, median 1.0 s per mission; the other phases also ran 1.3-1.5x
    slower in that run, so about 2.8x for level 2 itself); batched 44.2 s -> 44.0 s (batched passes plus ratings 8.1 s -> 11.7 s). Batched stays far ahead of the per-mission path
    (44 s against 115 s). The per-mission cost now grows with the number of players in the tour: `recompute_players` is 80% of the refresh.
  - `il2ks dev bench-ingest` times the passes separately ("batched level 2 and ratings" in the header; their phases are added to the table).
- **Identity fields** are recomputed, never summed: `first_seen` = earliest spawn, `last_seen` = latest sortie end, `current_name` = name on the
  latest spawn (so an old mission imported late never overwrites a newer name). `PlayerName` is rebuilt from the sorties.
- **Players are never deleted**; one whose sorties disappear keeps the row and URL with zero counters. Players who only ever flew as gunner
  (12 in the samples) also have a `Player` row with zero counters; their profile should say "gunner only" rather than look empty `[PROPOSED]`.
- **`GameObject`**: created from the catalog; on later runs `cls`, `is_playable` and `is_known` follow the catalog, but `display_name` is only
  replaced while it still equals `log_name` (admin edits survive, TD-24).
- **`Country`** rows are created when missing and never touched afterwards (admin-editable).
- **REDFOR / BLUFOR** come from the country code: 5xx = REDFOR, 6xx = BLUFOR (maintainer, 2026-10-03). Coalition numbers from `CNTRS` still
  decide friend or foe. A mission whose `CNTRS` mixes 5xx and 6xx countries in one coalition gets a warning on its run.
- `Mission.settings` stores the raw `SETTS` string (not parsed yet).
- **Assists are stored split** (2026-10-04): `PlayerSortie.assists_air` / `assists_ground` (`assists` is their sum), summed by `ingest.counters`
  into the same two columns on every counter table (`PlayerMission`, `Player`, `PlayerAircraft`, `PlayerTour*`, the pools, `AircraftStats`); the
  score reads `assists_air` only (doc 13). Migration 0034 adds the columns; the `assist_split` backfill fills them (below).
- `Kill` (PvP only) is keyed by **`(victim_sortie, killer_sortie)`**: a sortie is lost once, and a killer sortie gets either the kill or an assist
  on it. `credit`, `tick` and `via` are attributes.
- `PlayerSortie` JSON: `ammo` = `{loaded, left, used, left_after_loss, releases, hits, unattributed, ordnance}` (keys are only ever added; the
  docstring of `ingest/persist.py::_ammo_json` is the schema; rules in doc 13); `damage_breakdown` and `timeline` link counterpart player sorties by
  DB id; positions are `[x, y, z]`; timeline entries carry an ISO UTC `at`. **Timeline keys**: `tick`, `at`, `kind`, `detail`, `pos`,
  `counterpart`, and on the hit rows (`kind` `hit_given` / `hit_taken`, doc 13 "Timeline hits") also `damage` (summed DMG fraction, 4 digits),
  `lines` (damage lines in the burst), `ammo` (log name of the gun ammo, or the ordnance key; absent when no hit lay near) and `ammo_kind`
  (`ordnance` or `other`; absent for a gun). Other rows, and rows written before the hit rows existed, have none of these keys; the page copes.
- Two sorties with the same `(account_uuid, spawn_tick)` in one mission would violate the unique key; the mission then fails. Replay never
  produces them.
- **Empty missions** `[PROPOSED]` (2026-10-03): aborted server starts produce missions with no sorties (4 of 210 samples: three 3-second ones and
  one 46-minute mission with nobody on it). They're stored like any mission (so re-ingest and reprocess stay uniform) but the mission list
  only shows missions with `sorties_total > 0`.
- **`IngestRun` growth** `[PROPOSED]`: every reprocess adds one run per mission. Keep the newest few runs per mission (e.g. 5) and always the
  one that wrote the current archive; prune older ones at the end of `reprocess`.
- **Speed** (measured 2026-10-04, 210 sample missions, 18M lines, one Windows dev machine). **Round 1:** `ingest --from` 344 s (median 1.3 s per
  mission, p95 3.5 s; before the speed-up: 842 s, median 3.4 s). Per mission: parse 31%, replay 18%, level 2 15%, archive 15%, persist level 1 8%,
  commit and other 12%. What changed `[PROPOSED]`: the parser fast path (above); `ingest.dbutil.update_rows` instead of `bulk_update`, whose
  `CASE WHEN` expressions cost about half of the ingest; SQLite `wal_autocheckpoint=10000` (a mission commit is far bigger than the default 4 MB); a
  `closest()` fix in the replay.
  **Round 2** (same day, about **25-40% less CPU per mission**, identical rows: `dump-db` diffs and the equivalence tests): the direct event
  builders and chunked line splitting (parser, above); replay dispatch by event class (`state.feed` handles hit and damage events, 87% of a log,
  before the general `match`, and returns early for AI attackers with no player owner); `closest()` is tightened (no work for empty hit logs, bounds computed once); the archive
  is written at **DEFLATE level 3** (`ingest.archive.COMPRESS_LEVEL`: 18 ms instead of 43 ms for a 4.7 MB log, 349 KB instead of 265 KB, readable
  by any zip tool); and the batched writes below. `[PROPOSED]`
  **Round 3** (explosion bursts, above; `--cpu`, 210 missions, noisy machine, same rows: `dump-db` equal but `server_uid`): parse phase 35-42 s
  before, 30 s after; in-process parse + replay of 30 missions, back to back: 6.1-7.0 s before, 4.3-4.7 s after (parse -35%, replay about -5%).
  **Event construction** (2026-10-04, repos-dd): events are **statically frozen only** (pyright, `@event_class` =
  `dataclass_transform(frozen_default=True)`), at runtime plain `dataclass(slots=True, kw_only=True)`: frozen construction was about 2.5x
  slower. `_oid` is a `cast` instead of the NewType call and `Pos` tuples skip NamedTuple's Python `__new__`. `parse_lines` -19% on 6 logs,
  bench parse phase -10..-12%; `dump-db` identical. Events stay comparable, no longer hashable (nothing hashes them). `[PROPOSED]`
  **Batched writes:** `update_rows(model, rows, fields)` is `bulk_create(update_conflicts=True)`, an `INSERT ... ON CONFLICT (pk) DO UPDATE`
  with many rows per statement (SQLite 3.24+ and Postgres; the rows must be complete, loaded without `only`/`defer`); `update_partial_rows` stays
  one `UPDATE ... WHERE pk` per row for rows that carry only their pk and the changed fields (a rescoring of every sortie, the two link columns of
  new sorties). `PlayerMission` rows and the aircraft sides and payloads of a mission come from batched writes and one grouped query instead of
  `update_or_create` per player. Revisit the per-row updates if Postgres over a network ever becomes a production path.
  **Explosion lines** (measured 2026-10-04): **99.96% of explosion hits have a player-owned attacker** (bombs and rockets never act as attacker, doc 12;
  the owner is the carrier), so a replay **drop-filter** (skip explosion lines of AI attackers early) saves almost nothing and was **not adopted**.
  **Burst coalescing** (merging consecutive same-tick explosion lines of one attacker into one parser event; estimated 25-30% less parse + replay)
  is **built** (round 3, "Explosion bursts" above).
  **Non-std tokenizer libraries** (measured 2026-10-04 against the parser after bursts; 12 sample missions, 1.0M lines, 327K non-burst; best of 3-5, noisy
  machine; maintainer rule: allowed if it gives more than 10% on tokenizing). **Nothing adopted.** The per-AType regex step (head match + `fullmatch`
  + `groups()`) is only about **10% of parse** (0.125 s of 1.3 s); event construction is about 45%, burst detection about 22%. Engines on that step:
  stdlib `re` 0.125 s, `regex` 0.28 s (2x slower), `google-re2` 4.3 s (35x slower, str->bytes copy per call; its win_amd64 and manylinux wheels exist,
  0.5-0.6 MB), `pyre2` (wheels exist, but `import re2` fails on Windows here), `hyperscan` (wheels, 2-2.8 MB, but no capture groups, so no tokenizing).
  Bulk string kernels (`polars` 1.44, wheels for win/linux x86_64+aarch64, **~52 MB** with `polars-runtime-32`; `pyarrow` 28-53 MB): a prototype that
  finds the explosion bursts in bulk (extract groups, run boundaries, group by) took 0.46-0.51 s per 1.0M lines against about 0.5 s for today's string
  operations, so parse went from 2.1-2.2 s to 1.9-2.15 s: 0-10%, inside the noise, for a dependency that doubles the installer. No lexer library fits
  (`lark`/`ply` are pure Python and slower than `re`); our own C/Rust extension would need CI-built wheels for win_amd64 and linux x86_64/aarch64
  (cibuildwheel, three more jobs, a release step per Python bump) and could at most save the 10% regex share. Revisit only if event construction is
  moved out of Python.
  **Speed round 4: measurement, nothing adopted** (2026-10-04, 42 of the 210 sample missions = every 5th, 17 MB zipped, `bench-ingest --cpu`, after bursts).
  Per mission median **250-265 ms** CPU, p95 about 650 ms, 12.3-12.8 s for the 42 (the hundreds-of-milliseconds goal holds for the median).
  Phases (share of CPU): parse 30%, replay 29-30%, level-2 recompute 22-23%, persist level 1 10-11%, archive 5%, Elo ratings 1%, rest 2%.
  Query count: about 240 statements per mission; 64 are the per-sortie `UPDATE` that links the JSON columns (`update_partial_rows`, 1% of the
  time), 18 new-player `INSERT`s, 13 sortie `SELECT`s, the rest one or two statements per level-2 table. cProfile top (inflated by call
  overhead, shares of the profiled run): `ammo._Analysis.closest` 10% (12.7 s of 130 s; `_damage_pass` 12%, the whole ammo analysis 16%),
  `parse_lines` self time 4.7%, SQLite `commit` 6% (wall clock only: 190 ms per mission through the filesystem, not CPU),
  `bulk_create` SQL compilation 7%, zip `open`/`write` 7% (wall clock, antivirus). Tried and **rejected** (each needs >= 5% end to end):
  (1) memoising `closest` per (tick, unit, attacker), the many damage lines of one tick share an answer: replay 2.91 -> 2.70 s (best of 4, in-process A/B),
  7% of replay, about 2% end to end; (2) `orjson` for the JSON columns: all JSON encoding is 0.3 s of the 130 s profiled run (0.2%); (3) GC off or
  thresholds raised during parse + replay: 5.27 / 5.50 s against 5.33 s default (best of 6), 1-3%; (4) bigger `bulk_create` batches: Django caps a
  SQLite batch at 999 bound parameters (15 sortie rows) and the time is the ORM's value preparation, not the statement count (3351 inserts for 42
  missions, 80 per mission); (5) SQLite pragmas: WAL + `synchronous=NORMAL` already, a commit is no fsync. **Not tried, the only lever left of
  size:** parse + replay (60% of CPU, pure Python, no database) in a process pool one mission ahead of the persisting process. It needs
  `MissionResult` pickling, spawn on Windows and frozen builds, and an ordering rule for missions that share players, so it is a design decision,
  not a tweak; it would help `reprocess` and history imports, not the live one-mission-every-few-hours path. Measuring note: with other jobs on
  the machine the same run varied 1.9x in CPU time (23.9 s, 12.8 s, 7.6 s), so compare two variants in one process, alternating, best of N, and
  diff `dump-db` (it differs only by the random `server_uid` of each fresh data dir).
  Tools: `il2ks dev bench-ingest <dir> [--cpu]` (copies its input to a temp dir, times each phase; `--cpu` times process CPU instead of the wall
  clock so antivirus and other jobs do not skew it, but Windows resolves CPU time to about 15 ms, fine for sums and medians) and
  `il2ks dev dump-db` (every table as sorted JSON lines, to diff two runs). `ingest` refuses an `after_archive` move or delete when the logs dir is
  inside `sample_data/` (real player data).
- **Upgrade backfills** (`ops/migrate.py`, FR-OPS-3): after `migrate`, an upgraded database gets the data the new tables and columns need, once
  each: tours (`tours`), sortie scores (`scores`), per-type Elo and the prop/jet pools (`type_ratings`), the killboard by aircraft type and the
  per-tour / intercept matchups (`type_killboard`), `kills_air_intercept` from the stored timelines (`interception`), the air / ground assist
  split from the timelines (`assist_split`), rounds fired and gun hits from the stored ammo (`accuracy`), the streak history (`streak_runs`),
  `TourAircraftStats` (`tour_aircraft`) and the favourite loadouts (`builds`), the achievement facts rams, first blood, multi-kills
  and Elo peaks (`achievement_facts`), the loadout names looked up again from the payload ids (`payload_names`) and medals (`achievements`, then
  `achievement_tours` for the per-tour medals and rarity counts, after the rebuild). `_run_backfills` runs them in one transaction: **each `_check_*` fixes
  the level-1 sortie columns it owns (level 1) and returns whether level 2 needs a rebuild**; `rebuild_aggregates` then runs **at most once per
  upgrade** (it used to run once per step, up to three times) and all the markers are written together. Level-1 writes use `update_partial_rows`
  (rows with only the pk and the changed columns, one `UPDATE` per sortie instead of about 70 queries per sortie, Opus review #4), and
  timelines are streamed in chunks of 500.
  Each is recorded by name in `SiteSettings.backfills_done` after it ran (or was found unnecessary), because the data trigger alone cannot tell
  "never filled" from "legitimately empty" (a database with only zero scores, or no Elo encounters) and would rebuild after every later migration.
  Where a backfill needs a level-2 rebuild it calls `_rebuild_all`, the one place that passes every config section to `rebuild_aggregates`.
  These exist for pre-release databases and may go when the migrations are squashed before the first release (roadmap).
- **Verified end to end on the 210 sample missions** (2026-10-03, run three times; the last after the score inputs and Elo landed, with
  the same results and Elo stored = Elo recomputed for every player): no failures, zero bad lines and unknown
  keys, re-import skips everything with identical rows, `rebuild-aggregates` and `reprocess` reproduce level 2 **byte for byte** and keep every
  URL PK (PlayerAircraft included), and raw parts (split at their AType 15 headers) give the same result as the whole-mission import, including
  late parts after the originals were moved. Level-2 recompute for one mission's players takes 8–30 ms; a full rebuild 0.25 s.
