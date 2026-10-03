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
- Speed: 210 sample missions (1,235 MB, 18M lines) parse in 155 s (~8 MB/s); a median mission takes ~0.7 s.

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
- Heavy artillery is `vehicle`, self-propelled guns are `tank`, AA vehicles and AA platform cars are `aaa`: best guesses, admin-editable (TD-24).
- **Payloads**: 99.3% of sample spawns resolve. Missing payloads (new ones arrive with game updates) must never break a page: the sortie shows the
  raw payload ID (OQ-25 tracks extracting the rest). `payload_aliases.csv` becomes admin-overridable together with object names (it2, TD-24).

## Ingest jobs

**Config** (`config.py`, TD-11)
- Sources, later wins: defaults → `il2ks.toml` → `IL2KS_<SECTION>_<KEY>` env vars. File lookup: `--config`, `IL2KS_CONFIG`, `./il2ks.toml`,
  `<data dir>/il2ks.toml`. Relative paths resolve against the file's folder.
- A fully commented `il2ks.toml` template with every key and default ships in the package (for `il2ks setup`); a test keeps it equal to the
  code defaults.
- **Server timezone** (TD-15): `[server] timezone` (optional, default unset) → `TZ` env → `/etc/localtime` → UTC. Windows has no IANA name, so
  Windows admins set it (or run DServer on UTC); `il2ks doctor` should warn.
- **Server UID** (TD-17): `[server] uid`, else `<data dir>/server_uid.txt` (generated once).

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
- `il2ks reprocess` takes the missions from their newest archive-writing run plus archives without any run (rebuilding a lost DB). Filters:
  `--mission UID` (repeatable) and **`--since DATE` / `--until DATE`** (inclusive, by the mission UID's server-local date; maintainer use case:
  "scoring changed, reprocess the last 6 months"). Parse and replay run in low-priority worker processes; the main process is the only writer.
  A reprocess failure leaves the previous rows in place and never schedules a retry.
- Planned commands (`setup`, `web`, `run`, `doctor`, `createadmin`, `backup`, `restore`) exist as stubs that say which requirement they belong to.

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
- **Level-2 updates = recompute the affected players from level 1** (maintainer, 2026-10-03; replaces "subtract old, add new"). After a
  mission's level-1 rows are written, every player who had or has a sortie in it gets their totals, per-aircraft rows and identity fields
  recomputed from their level-1 rows. `rebuild-aggregates` uses the same code for all players. Why: no delta arithmetic to get wrong, and a
  re-ingest can't drift from a rebuild. Cost grows with a player's history; in it2 the recompute is bounded to the mission's tour plus the
  all-time row.
- **Identity fields** are recomputed, never summed: `first_seen` = earliest spawn, `last_seen` = latest sortie end, `current_name` = name on the
  latest spawn (so an old mission imported late never overwrites a newer name). `PlayerName` is rebuilt from the sorties.
- **Players are never deleted**; one whose sorties disappear keeps the row and URL with zero counters.
- **`GameObject`**: created from the catalog; on later runs `cls`, `is_playable` and `is_known` follow the catalog, but `display_name` is only
  replaced while it still equals `log_name` (admin edits survive, TD-24).
- **`Country`** rows are created when missing and never touched afterwards (admin-editable).
- **REDFOR / BLUFOR** come from the country code: 5xx = REDFOR, 6xx = BLUFOR (maintainer, 2026-10-03). Coalition numbers from `CNTRS` still
  decide friend or foe. A mission whose `CNTRS` mixes 5xx and 6xx countries in one coalition gets a warning on its run.
- `Mission.settings` stores the raw `SETTS` string (not parsed yet).
- `Kill` (PvP only) is keyed by **`(victim_sortie, killer_sortie)`**: a sortie is lost once, and a killer sortie gets either the kill or an assist
  on it. `credit`, `tick` and `via` are attributes.
- `PlayerSortie` JSON: `ammo` = `{loaded, left, hits}`; `damage_breakdown` and `timeline` link counterpart player sorties by DB id; positions are
  `[x, y, z]`; timeline entries carry an ISO UTC `at`.
- Two sorties with the same `(account_uuid, spawn_tick)` in one mission would violate the unique key; the mission then fails. Replay never
  produces them.
- **Empty missions** `[PROPOSED]` (2026-10-03): aborted server starts produce missions with no sorties (4 of 210 samples: three 3-second ones and
  one 46-minute mission with nobody on it). They're stored like any mission (so re-ingest and reprocess stay uniform) but the mission list
  only shows missions with `sorties_total > 0`.
- **`IngestRun` growth** `[PROPOSED]`: every reprocess adds one run per mission. Keep the newest few runs per mission (e.g. 5) and always the
  one that wrote the current archive; prune older ones at the end of `reprocess`.
- **Verified end to end on the 210 sample missions** (2026-10-03): no failures, zero bad lines and unknown keys, re-import skips everything with
  identical rows, `rebuild-aggregates` and `reprocess` reproduce level 2 exactly and keep URL PKs, and raw parts (split at their AType 15
  headers) give the same result as the whole-mission import, including late parts after the originals were moved.
