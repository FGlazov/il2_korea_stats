# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps**
Is there a source for `WM` weapon-modification names, like the payload file? Payload 59 for the F-51D is missing from the payload file.
(Redistribution is settled: the payload file ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.

## Iteration 1 implementation decisions to review (2026-10-03)

Decisions made while writing the first ingest prototype that the design docs don't cover. Each one is in the code already;
confirm, change, or reject. IDs `OQ-I1-*` are for this batch only.

**OQ-I1-1 Contracts between layers.** Events are frozen dataclasses in `core/logparse/events.py` (one per AType, unmapped keys in
`extra`, unused ATypes as `GenericEvent`). The replay output is `core/replay/result.py` (`MissionResult` with `MissionInfo`,
`SortieResult`, `KillResult`), with all times in ticks; `ingest` turns ticks into UTC datetimes.

**OQ-I1-2 `KillResult` covers every kill with a player on at least one side**, not only PvP. `ingest` stores only PvP rows in `Kill`
(doc 06) and uses the rest for sortie counters and timelines.

**OQ-I1-3 Extra sortie columns not in doc 06**: `is_death`, `is_plane_lost`, `is_captured` (so level 2 sums booleans instead of re-deriving
rules from outcome and pilot fate), `takeoffs`, `landings`, `payload_name`, spawn position columns, and `account_uuid` + `spawn_tick` on
`PlayerSortie` (the natural key). Outcome adds a `mission_ended` value next to the pilot fate of the same name.

**OQ-I1-4 Counters shared by `PlayerMission`, `Player` and `PlayerAircraft`**: sorties, flight time, air and ground kills, assists,
deaths, planes lost, bailouts, suspected early bailouts, captures, takeoffs, landings (doc 06 lists "..."). Mission keeps
`players_total`, `sorties_total`, `redfor_sorties`, `blufor_sorties`, `kills_air`, `kills_ground`.

**OQ-I1-5 `Kill` natural key** is `(killer_sortie, victim_sortie, tick, credit)`.

**OQ-I1-6 Ammo**: v1 stores loaded and left counts (AType 10 and 4) and hits per ammo type (no `explosion`). The FR-WEB-18 damage
attribution and ordnance labelling are left for iteration 1.x.

### Review first (the decisions with the most effect on numbers)

- **OQ-I1-7 Static objects count as ground kills** (fences, storage stacks): 52k ground kills vs 2.4k air kills over the samples.
  `KillResult.victim_type` allows filtering by class later. Should statics score? (replay, `kills.py`)
- **OQ-I1-8 Kill credit is resolved for every lost player aircraft**: an aircraft shot up that later crashes on its own is credited to the
  shooter (consistent with `loss_cause = attacker`), not only after bailouts or disconnects. Assist = any other damager with ≥ 1% damage. (replay)
- **OQ-I1-9 Only pilot sorties feed counters.** Gunner sorties are stored but not counted in player, aircraft or mission totals until gunner stats
  exist (FR-WEB-14). (persist, `ingest/counters.py`)
- **OQ-I1-10 Sample distributions differ slightly from doc 12**: bailouts 12.0% of sorties that took off (doc 12.6%), suspected early bailouts
  3.2% (doc 2.7%; cause not found), `mission_ended` 9.5% of pilot sorties, structural failures 392 (doc 391), loss cause attacker/self 51/49,
  ~21 pilot deaths per mission (doc ~17), 974 sorties (8%) with outcome `unknown` (in-flight disconnects without recent damage).
- **OQ-I1-11 Ingest completeness has an extra setting** `ingest.settle_seconds = 60`: a mission with AType 7 is complete only once no part has
  been written for that long (cleanup lines follow AType 7). (ingest, `discover.py`)
- **OQ-I1-12 Server timezone default** is `[server] timezone`, else `TZ`, else `/etc/localtime`, else UTC. Windows has no IANA name, so Windows
  admins must set it (or run DServer on UTC); `il2ks doctor` should warn. (ingest, `config.py`)
- **OQ-I1-13 `IngestRun` doesn't store why a mission counted as complete** (AType 7 vs idle timeout). FR-ING-18 wants admins to see
  "completed only by idle timeout"; that needs a new field. `Mission.completed_cleanly` = AType 7 seen.

### All decisions by area

The full lists the agents recorded, with code locations. Each bullet is a decision to confirm or change.
#### core.logparse

- **AType 27/28 are "ignored", not "unknown".** They're documented in doc 12 and deliberately dropped (maintainer, 2026-10-02), so they
  become a `GenericEvent` and are counted in `ParseStats.ignored_atypes`, not `unknown_atypes`. The never-seen 17, 22, 23, 29 (and anything
  new) count as unknown, because their appearance is news. Where: `parser.IGNORED_ATYPES`, `parse_lines`. (Additive new field
  `ParseStats.ignored_atypes`.)
- **Plain `[0].txt`: raw part or whole-mission archive?** The names are identical, so the caller decides: `group_mission_files(paths,
  txt_as="parts" | "archive")`, default `"parts"` (production reads raw parts, FR-ING-1); history import passes `"archive"`. A `[N].txt`
  with N >= 1 always marks the mission as raw parts (its `[0].txt` is then part 0 whatever `txt_as` says). `.txt.zip` is always an archive.
  Where: `files.group_mission_files`.
- **Precedence when several inputs exist for one mission UID**: raw parts beat any archive (they're newer); a plain `.txt` archive beats a
  `.txt.zip` (same content, no decompression). Same path twice counts once; two paths with the same UID and N keep the first in sorted path
  order. Names not matching `missionReport(YYYY-MM-DD_HH-MM-SS)[N].txt[.zip]` are ignored (`*.weather.json`, etc.).
- **Mission UID** = the timestamp inside the parentheses, as a string, e.g. `2026-09-19_22-34-13` (doc 06). Parts sort numerically by N.
- **Free-text values are anchored on the next known key** of that AType (`NAME`, `TYPE`, `SKIN` in 10; `TYPE`, `NAME` in 12; `MFile` in 0),
  matching ` KEY:` or ` KEY(`. A nickname that itself contains e.g. ` TYPE:` would be cut there; we accept that (never seen in 18M lines).
  Where: `parser._Spec.terminators`, `tokenize`.
- **A bad line is any line with a malformed token, a missing required key, an unconvertible value, a duplicate key, or unbalanced
  parentheses.** It raises `ParseError` from `parse_line` and is counted in `lines_bad` (with a warning, max 200 warnings per mission,
  each quoting at most 200 chars of the line) from `parse_lines`. Where: `parser.MAX_WARNINGS`, `MAX_WARNING_LINE_CHARS`.
- **Unknown ATypes never fail**, even when their text can't be tokenized: the remainder is kept under `GenericEvent.fields["#raw"]`.
- **Unknown keys**: any token not read by the AType's spec goes to `extra`. It's counted in `unknown_keys` as `"<atype>:<KEY>"` unless it's a
  documented trailing key (`MID` on 12, `ROUNDS`/`POINTS` on 0, `TARGETS()/OBJECTS()/PLANES()/MTARGETS()/MOBJETS()` on 8, `IDS()` on 9),
  which still sit in `extra` but are not "unknown". Where: `_Spec.known_extra`.
- **Blank lines are skipped and not counted** in `lines_total`. Lines are stripped, so CRLF and trailing spaces are fine.
- **`KEY: value` (space after the colon)** takes the next word as the value unless that word is itself a key, so `MID: GType:2` gives
  `MID=""` and `ROUNDS: 1` gives `1`.
- **`stats.log_version`** is the first AType 15 `VER` of the mission. A later different `VER` adds a warning (not a bad line).
- **Encoding**: UTF-8 with `errors="replace"`, a leading BOM is dropped, line endings normalized. A stray byte can at most change one value.
- **A zip without exactly one `.txt` raises `ValueError`** from `read_mission_lines` (a file-level problem, not a line problem; the ingester
  should mark that mission failed).
- **Speed**: all 210 sample missions (1,235 MB, 18.06M lines) parse in 155 s, about 8 MB/s; the slowest mission took 2.5 s, a median
  ~6 MB mission about 0.7 s.

#### catalog, timeutil, logsetup (iteration 1)

##### core.catalog

- **Crew bots, parachutes, ejection seats and spotters get class `unknown` but `is_known=True`.** `ObjectClass` (contract, mirrors
  `db.models.ObjectClass`) has no crew/equipment class and the contract is frozen. They are in `objects.csv` (so the coverage test
  passes and `is_known` is true), with readable display names (`Pilot (USAF, jet)`, `MiG-15bis ejection seat`). Affected rows:
  `Bot*`, `B29_CrewGroup*`, `CParachute`, `ESeat_*`, `Spotter`, `VehicleTurret`, `VehicleRangefinderTurret`. A `crew` class would be a
  one-line contract change if the replay wants to tell them apart; `is_known=False` is reserved for types missing from the data.
  Code: `core/catalog/data/objects.csv`.
- **Lookups normalise names** (`canonical_type_name`): a trailing block suffix `[group,index]` is removed (`GAZ_63[64606,0]` ->
  `GAZ_63`) and `CParachute_<n>` becomes `CParachute`. Case is ignored for matching, original case kept for the stored `log_name`.
  Why: static block objects and parachutes carry per-instance suffixes in `TYPE:`. Code: `loader.canonical_type_name`.
- **Unknown type's display name is the canonical (suffix-stripped) log name**, not the raw string. Code: `Catalog.lookup`.
- **Turrets**: `Turret_*` and `Multiturret_*` are `gunner`. Only `Turret_IL10` is `is_playable` (the only one seen in AType 10);
  B-29 and Tu-2 turrets are not. `Tu-2` and both `B 29` / `B-29` spellings are `bomber`, not playable. Code: `objects.csv`.
- **Static copies of vehicles and ships** (`Parked <aircraft>`, `Box Car A`, `Tanker Ship A`, `GAZ_63`, `Willys_MB`, ...) are
  `static` (scenery blocks), while the AI-driven `GAZ-63`, `Willys MB`, `Tank Car`, ... are `vehicle`. Why: static blocks are the
  objects that `[g,i]` block suffixes are attached to.
- **Ordnance** = drop/wing-tip tanks, JATO boosters, napalm and rockets (the stores seen as AType 12 spawns in the samples). No
  bomb types appear as AType 12 spawns there.
- **Heavy artillery** (howitzers, ML-20) is `vehicle`, self-propelled guns (SU-76M, ISU-122, M7 Priest, M40 GMC) are `tank`,
  AA-capable armoured vehicles (M16/M19 MGMC) and all `Platform Car AA*` are `aaa`. Best guess; the class is only a default
  that the admin can edit (TD-24).
- **`payload_aliases.csv`** maps log aircraft names to `payloads.csv` vehicle keys (`F-86A-5` -> `f-86a`, `B 29` -> `b-29`).
  Aircraft without an alias are tried under their own case-insensitive name. Code: `Catalog.payload`.
- **Duplicate object names (case-insensitive) raise `ValueError` when building a `Catalog`**, to catch data errors early.
  Code: `Catalog.__init__`.
- **Payload coverage in the 210 sample missions is 99.3%** (spec: >= 99%). The sample test needs `sample_data/`.

##### ingest.timeutil (TD-15)

- **Spring-forward gap**: the wall time is shifted forward by the gap (02:30 -> 03:30 for a 1 h gap) with a warning. Code:
  `resolve_mission_start`.
- **Fall-back hour with a hint**: the candidate closest to `hint_utc` wins, ties go to the earlier instant. A naive `hint_utc`
  is taken as UTC. Without a hint: earlier instant (fold=0) and a warning.
- **`parse_mission_uid`** is public (returns the naive local datetime) for reuse by ingest.

##### logsetup (TD-27, NFR-OBS-1)

- JSON line fields: `time` (UTC, ISO 8601, milliseconds), `level`, `logger`, `message`, `process`, any `extra=` fields,
  `exception`, `stack`. Non-serialisable extras are stringified.
- Rotation: 10 MB x 5 backups. The log file is created lazily on the first record.
- Idempotent: calling again replaces the handlers installed earlier by this module (other handlers are left alone) and re-applies
  the level to the root logger. An unknown level name raises `ValueError`.

#### core.replay

##### Structure

- **`feed()` only records facts; every rule runs at resolve time** (`snapshot()` = provisional, `finish()` = final, same code, so streaming and
  batch are identical and lookahead rules just see the whole history). Where: `state.py` (record), `resolve.py` (assemble), `judge.py` (one
  `Verdict` per sortie), `fate.py` (one function per rule, TD-16), `credit.py` / `kills.py` (kill and assist credit), `breakdown.py`
  (exchanges, hits per ammo, timeline). A snapshot is O(all recorded damage lines): meant for seconds-scale polling, not per event.
- **Object identity is the Python object, not the ID.** AType 12 for a known ID updates it (re-link, pilot `PID:-1` keeps its parent). A *different* type
  on a known ID creates a new object, unless the old one belongs to a still-open sortie (then it's an update). **The game recycles IDs a lot**:
  in 7 sample missions, 15,429 re-declarations carried another type than the object they re-used (47,991 carried the same), mostly statics and
  vehicles. So a same-type re-declaration of an object that was already destroyed (AType 3) and isn't a sortie aircraft is a new object too, and a
  new sortie never re-uses a destroyed or differently-typed object. (Without this, ground kills of re-spawned objects were swallowed.) Undeclared IDs seen in other
  events get a placeholder object (type ""), like il2_stats did. Where: `Replay._on_object_spawn`, `_get`.
- **Static block suffix `[g,i]` is stripped from the type** before lookup and in `object_types_seen` (`model.normalize_type`).
- **Crew bots are not in `object_types_seen` / `unknown_object_types`** (they're `cls=unknown, is_known=True` in the catalog anyway).
- **Zero-damage AType 2 lines are ignored** (il2_stats did). **Damage after an object's AType 3 is ignored** (il2_stats did). Explosion hit lines
  are dropped at `feed()` (never counted, TD-08). Hit lines are kept in memory per target for rule 5 and the per-ammo counts.

##### Sortie scope and mission end

- **Aircraft destruction belongs to the sortie** if its AType 3 comes before the sortie end, within `post_end_destroy_window_s` = 5 s after it
  (new config; the shot-down shape logs AType 4 before AType 3. Measured on 14 missions, for player aircraft with a normal AType 4: 236 kills
  before it, 127 within 1 s after it, none from 1 to 60 s, 3 later), or at any
  time if the pilot left an airborne aircraft (AType 4 `PLID:0`, no AType 4, or a disconnect: the FR-ING-22 abandoned aircraft). A pilot kill
  counts within the same 5 s window. Where: `fate.aircraft_loss`, `pilot_death_tick`.
- **`mission_ended`** needs a *normal* AType 4 (`PLID` != 0) between the first AType 7 and +5 s (`mission_end_sortie_window_s`). A `PLID:0` end or a
  removal without AType 4 near mission end is a real pilot exit or a disconnect. Destruction or pilot death at/after the first AType 7 is
  despawn cleanup (the server destroys everything), not combat, and is ignored for such a sortie. A sortie that never took off keeps
  `not_taken_off`. A landed player sitting at mission end is `mission_ended` too (follows doc 12: "10% of sorties"). Open sorties at `finish()`:
  `mission_ended` if AType 7 was seen, else `in_flight`/`landed` with `pilot_fate = unknown`. Where: `fate.forced_by_mission_end`.

##### Fate, outcome, status

- **Fate ladder** (`fate.pilot_fate_of`): gunner with AType 18 -> `bailed_out`/`event`; pilot bot killed -> `in_aircraft`; forced by mission end ->
  `mission_ended`; AType 4 `PLID:0` -> bailout rule v2 (`bailed_out`/`inferred`), else `exited_on_ground`/`inferred` (`unknown` when the pilot's
  final position is missing); no AType 4 (or AType 21 within 30 s while the aircraft was airborne at a plain end) -> `disconnected`, unless an attacker
  destroyed the aircraft (then `in_aircraft`); plain AType 4 -> `in_aircraft`/`event`. `disconnected` bool on the sortie = AType 21 within +-30 s of the
  end (or removal without AType 4): a landed player who leaves after despawning has `disconnected = True` but fate `in_aircraft`.
- **Bailout rule v2**: "last known aircraft position" = the AType 3 position if destroyed, else the latest position recorded for the aircraft
  (spawn, damage, kill, wheels, takeoff, landing, re-declaration). Rule 5 counts hits and damage on aircraft *and pilot bot* from any non-self attacker;
  "self" = the AID:-1 environment or any object of the sortie. Rule 7 uses `end >= first AType 7 - 60 s`. Where: `fate.bailout_v2`,
  `suspected_early_bailout`.
- **Disconnect (FR-ING-21)**: a disconnect without damage in the 120 s before it is neither death nor loss, and a later destruction of the abandoned
  aircraft is ignored (`loss = None`). With damage: death (`pilot_status = dead`), plane lost, `loss_cause` by attacker involvement. Where:
  `judge.judge`.
- **Pilot death** = pilot bot AType 3 in scope, or fate `in_aircraft` with the aircraft lost (the pilot went down with it). `is_plane_lost` = aircraft
  destroyed in scope, pilot killed, bailed out, or disconnect death.
- **Outcome ladder** (`judge._outcome`): any loss/death/bailout -> `shot_down` if `loss_cause = attacker` else `crashed` (a bailout with no AType 3 is
  `crashed`/`shot_down` too, as in il2_stats); not taken off; `mission_ended`; open sortie -> `in_flight`/`landed`; disconnect without damage ->
  `unknown` if airborne else landed/ditched; airborne at a normal end (despawned in the air) -> `in_flight`; on the ground -> `landed` if the last
  landing was within 4 km of a friendly airfield (`areas.AIRFIELD_RADIUS_M`, il2_stats), `ditched` otherwise. No friendly airfield logged -> `landed`
  (can't tell).
- **Capture** (`is_captured`, `pilot_status = captured`): an alive pilot whose bailout position, ground-exit position or last landing position lies in
  an enabled influence area (AType 13/14, 2D polygons) of another non-neutral coalition. Not set for mission-ended sorties.
- **`loss_cause`**: `attacker` if the kill line names an attacker that isn't the sortie itself, or any attacker hit/damage on aircraft or pilot
  happened before the loss; `self` otherwise; `none` when nothing was lost. **Structural failure** uses the first AID:-1 damage line on the aircraft
  (so no self-damage line = not flagged) and the first AType 31/6 after the AType 3 (none = not flagged, as documented).
- `damage_taken` = sum of the aircraft's own damage lines up to the loss tick (or sortie end), clamped to 1. Aircraft status `destroyed` only when
  an AType 3 was in scope.

##### Kills and credit

- **Credit is resolved for every lost player aircraft, not only bailouts/disconnects**: explicit AID wins (unless it is the sortie itself); with
  AID:-1 the attacker with most damage wins; every other damager with >= `assist_min_damage` (1%, new config; il2_stats used > 1%, and gave an
  assist to the second damager only) gets an assist. Damage to the pilot bot and turrets counts as damage to the aircraft (il2_stats did). So an
  aircraft shot up and later crashing on its own is credited to the shooter, consistent with `loss_cause = attacker`. `via` = `direct` (explicit killer
  or no pilot exit), `abandoned_aircraft` (bailout / ground exit), `disconnect`. `credit = "shared"` is never emitted. Where: `credit.credit_kill`,
  `kills.resolve_kills`.
- **Disconnect or bailout without an AType 3** for the aircraft still creates the victim record (tick = sortie end), so damage-based credit applies
  (FR-ING-21/22 say "credit if an attacker caused the recent damage"; credit uses all attacker damage in the sortie, not only the last 2 minutes).
- **Victims**: every destroyed object that isn't a crew bot, a gunner turret or `ordnance` class, **including static objects** (fences, storage
  stacks: 52k "ground kills" over the samples vs 2.4k air). `KillResult.victim_type` lets ingest filter by class later; sortie counters
  `kills_ground` currently include them. Decide in the aggregation layer whether fences should score.
- **Gunners**: a player gunner's kills go to the gunner's own sortie only (not copied to the pilot); an AI turret on a player's aircraft credits the
  pilot. A gunner is never a kill victim. Gunner flight state comes from the parent aircraft.
- **Friendly fire**: killer and victim coalition equal and non-zero -> `is_friendly`; not counted in `kills_*`/`assists`; timeline `friendly_fire`.
- A player victim killed by the environment with nobody to credit still gets a `KillResult` (killer `None`) so the death is on record.

##### Breakdowns

- Damage exchanges and hits are grouped per counterpart (`object_type`, `sortie_index`), by the aircraft root (so crew and turrets fold into the
  aircraft). Self damage is left out. Hits per ammo type count every non-explosion hit line the sortie gave or received. Damage-to-ammo attribution
  (FR-WEB-18) and ordnance counting are **not** implemented (iteration 1.x).
- Timeline kinds: `spawn`, `takeoff`, `landing`, `kill`, `assist`, `friendly_fire`, `shot_down`/`destroyed`, `bailout`, `disconnect`, `sortie_end`.
- `takeoff_tick` for an air start = the spawn tick (`takeoffs` counts AType 5 only). `flight_time_s` = sum of airborne intervals (AType 5 to 6 or to the
  end). Both stop at the aircraft's loss tick when it was destroyed first: logs write AType 6 for the falling wreck, which isn't a landing.

##### Config and contract changes

- `ReplayRules`: added `post_end_destroy_window_s = 5.0` and `assist_min_damage = 0.01` (defaults, additive). `result.py` and `events.py` unchanged.

#### ingest.persist and ingest.aggregates

- **Counted sorties = pilot sorties only.** Gunner sorties are stored on level 1 (`role=gunner`) but feed no counters
  (`PlayerMission`, `Player`, `PlayerAircraft`, mission counters), because gunner stats come later (FR-WEB-14). Why: avoids
  double counting a crew's flight time and kills. Code: `ingest/counters.py` `COUNTED_ROLES`.
- **One counter registry.** `SORTIE_COUNTERS` (ORM aggregates over `PlayerSortie`) is the only definition of the counters.
  `PlayerMission` and `PlayerAircraft` are built by grouping sorties with it; `Player` totals are sums of `PlayerMission`.
  A test checks it covers exactly the fields of `db.models.Counters`. Code: `ingest/counters.py`.
- **Incremental update = subtract old, add new.** On re-ingest, `subtract_mission` applies the old level-1 contribution as
  negative deltas (`F(field) - value`), the mission is rewritten, `add_mission` adds the new one. Float residue: players
  left with zero sorties get `flight_time_s` reset to 0 and empty `PlayerAircraft` rows are deleted (a rebuild wouldn't create
  them). Rows that still exist keep their PK (`save_mission` calls `subtract_mission(prune=False)` and prunes afterwards).
  Code: `ingest/aggregates.py`, `ingest/persist.py:save_mission`.
- **Identity fields are recomputed, not summed.** `Player.first_seen` = earliest sortie spawn, `last_seen` = latest sortie end,
  `current_name` = name on the latest spawn by time (ties: higher sortie PK), so an older mission ingested later never
  overwrites a newer name. `PlayerName` rows (per distinct name: first/last seen) are rebuilt from the sorties. Code:
  `aggregates.refresh_players`.
- **Players are never deleted.** A player whose only sorties disappear after a re-ingest keeps the row (URL stays stable,
  FR-WEB-13), with zero counters; identity fields keep their last values.
- **`GameObject` registration rule.** Created from `catalog.lookup()` (display name falls back to the log name when unknown).
  For existing rows `cls`, `is_playable` and `is_known` follow the catalog (so a catalog update fixes old unknowns), but
  `display_name` is only replaced while it still equals `log_name` (the auto-registered placeholder), so admin edits survive
  (TD-24). Registered types: `object_types_seen`, `unknown_object_types`, every sortie aircraft, and every kill victim/killer type.
  Code: `persist.register_game_objects`.
- **`Country` rows**: created when missing with `catalog.coalition_name(coalition)`; existing rows are never touched
  (admin-editable, FR-ADM-5). Code: `persist.register_countries`.
- **`Mission` columns.** `ended_at` = `started_at` + `end_tick`/50; `duration_s` = `end_tick`/50; `settings` is stored as
  `{"raw": <SETTINGS string>}` (the raw string isn't parsed yet); `countries` as `{"501": 1, ...}`; `is_hidden` is not touched
  on re-ingest. Code: `persist._mission_fields`.
- **Mission counters** (`sorties_total`, `redfor_sorties`, `blufor_sorties`, `kills_*`) count pilot sorties like the player
  totals; coalition 1 = redfor, 2 = blufor; `players_total` counts every player with a sortie of any role.
- **`PlayerMission`** exists for every player with a sortie of any role (counters 0 for a gunner-only player); coalition = that
  player's first sortie in the mission. Rows of players no longer in the mission are deleted on re-ingest.
- **`PlayerSortie`**: `air_start` = `spawn_type == "air"`; `payload_name` = `catalog.payload(type, payload_id).readable_name`
  (empty when unknown, truncated to 128); `ammo` json = `{loaded, left, hits}`; `damage_breakdown` and `timeline` entries link
  a counterpart player sortie by `sortie_id` (the DB PK, filled after the rows have PKs), positions as `[x, y, z]`, timeline
  entries also carry an ISO UTC `at`. Code: `persist._upsert_sorties` and the `_*_json` helpers.
- **`Kill` is upserted** by `(killer_sortie, victim_sortie, tick, credit)` (the unique constraint) so re-ingest keeps PKs and
  deletes the rest; only rows with both sortie indexes set are written. Code: `persist._replace_kills`.
- **Two sorties with the same `(account_uuid, spawn_tick)` in one result** are not handled: the unique constraint raises and
  the caller's transaction rolls back (surfaces as a failed `IngestRun`). The replay layer is expected not to produce them.

#### ingest jobs (discovery, archive, lock, runner, CLI, config)

##### Config (`config.py`)

- **Config sources, later wins**: defaults, then `il2ks.toml`, then `IL2KS_<SECTION>_<KEY>` env vars (`[logs] dir` is `IL2KS_LOGS_DIR`; top-level
  keys drop the section: `IL2KS_DATA_DIR`, `IL2KS_LOG_LEVEL`). Lists are comma-separated in env vars. Why: TD-11 says env overrides for Docker.
  Where: `config._Reader`.
- **Which file**: `--config`, else `IL2KS_CONFIG`, else `./il2ks.toml`, else `<data dir>/il2ks.toml`, else none (defaults + env). Relative paths
  in the file resolve against the file's folder, in env vars against the working directory. Where: `config.find_config_file`.
- **Sections and defaults** (all in `Config`): `logs.dir` (none: `ingest`/`watch` refuse to run without `--from`), `logs.after_archive = "move"`,
  `logs.move_to` (default `<data dir>/ingested-logs/<mission uid>/`), `logs.remote = false`; `ingest.idle_minutes = 10`, `ingest.settle_seconds = 60`,
  `ingest.stable_seconds = 60`, `ingest.watch_interval_s = 30`, `ingest.retry_backoff_minutes = [5, 30, 120]`; `[replay]` takes every `ReplayRules`
  field by name; `[server] timezone`, `[server] uid`; `log_level = "INFO"`; `data_dir`.
- **Server UID storage (TD-17)**: `[server] uid` if set; else `<data dir>/server_uid.txt`, generated (uuid4) on first use and then kept. Why: the
  future `il2ks setup` can copy it into `il2ks.toml`; until then it must be stable across runs. Where: `config.stored_server_uid`.
- **Server timezone default (TD-15)**: `[server] timezone`, else the `TZ` env var, else the `/etc/localtime` symlink (Linux), else `UTC`. Windows
  has no IANA name without a Windows-to-IANA table, so Windows admins set `[server] timezone` (or run DServer on UTC, doc 07). `il2ks doctor`
  should warn when a Windows machine is on UTC by fallback. Where: `config.detect_os_timezone`.
- **Completeness has one extra knob, `ingest.settle_seconds` (60)**: AType 7 is followed by cleanup lines (doc 12 Timing: only 14 of 210 files end
  with 7), so a mission with AType 7 counts as complete only once no part was written for this long. Where: `discover.completeness`.

##### Discovery (`discover.py`)

- **Completeness order**: remote mode first requires every part unmodified for `stable_seconds` (a newer mission existing doesn't prove the copy
  finished, FR-ING-16); then "newer mission's `[0]` exists", then idle >= `idle_minutes`, then AType 7 (searched as raw bytes `AType:7` not followed
  by a digit, in the last 3 parts only) plus `settle_seconds`. Whole-mission archives are complete at once (after the stability check in remote
  mode). Where: `discover.completeness`, `MISSION_END_SCAN_PARTS`.
- **Fingerprint** = sha256 over `(file name, size, mtime_ns)` of the mission's files in order; folder-independent (FR-ING-18). A re-copy with new
  mtimes counts as "changed" and re-ingests (harmless: upsert). Where: `discover.fingerprint`.
- **Run history classification** (`discover.classify`): no run -> new; last ok/skipped -> unchanged or changed (fingerprint); last failed -> retry
  when `next_retry_at` has passed, or at once when the fingerprint or the il2ks version differs; failed with no `next_retry_at` -> "gave up" (stays
  failed until files change, a new version is installed, or `reprocess --mission`). Skipped decisions are not recorded as `IngestRun` rows
  (they'd flood the table every watch tick); they're counted in the run summary and logged.
- **Retry accounting**: `attempts` counts consecutive failures with the same fingerprint and version; any change resets it to 1. Schedule: attempt 1
  fails -> retry in 5 min; 2 -> 30 min; 3 -> 2 h; 4 -> stop (`next_retry_at = NULL`). So a mission gets at most 4 automatic attempts. Where:
  `discover.next_retry`, `runner.ingest_mission`.

##### Archive (`archive.py`)

- **Layout**: `<data dir>/archive/YYYY/MM/missionReport(<uid>)[0].txt.zip` (YYYY/MM from the mission UID, i.e. the server's local time), one zip entry
  `missionReport(<uid>)[0].txt` with all parts concatenated in order. Same names as `sample_data/` and the il2_stats backups, so
  `group_mission_files` reads our archives like any import (FR-ING-13). DEFLATE level 6 (stdlib, readable everywhere). `IngestRun.archive_path` is
  stored relative to the data dir (portable when the data dir moves), with `/` separators.
- **Checksums**: `IngestRun.archive_sha256` is the sha256 of the zip file itself (reconcile and reprocess compare it); the writer also verifies the
  zip CRCs and a sha256 of the uncompressed content, in a temp file, before atomically renaming it into place. A failed verification leaves any
  earlier archive untouched.
- **Line break between parts**: if a part doesn't end with a newline, a CRLF is inserted so lines never merge (never seen in samples; defensive).
- **Late parts after the originals were moved (FR-ING-18)**: the new archive = the previous archive (must still match its recorded checksum) + the
  new parts, so it holds the whole mission. If the previous archive is gone, the mission fails with a clear error. Parts that reappear although they
  are already archived are not appended twice (warning on the run). Where: `runner.plan_sources`.
- **After-archive handling**: only after the DB commit, so a crash between never loses anything; failures to move (Windows: file still open) are
  collected, logged, and retried on the next run, because "unchanged" missions still get their leftover originals disposed. `move` puts files in
  `<move_to>/<mission uid>/` and replaces a leftover with the same name. `ingest --from` **never** moves, deletes, or otherwise touches its source,
  whatever `logs.after_archive` says (there is no flag to change that). Where: `archive.dispose_originals`, `runner._ingest_locked`.
- **Reconcile (FR-ING-8)**: an ingested mission whose source files are still in the log folder (keep mode, or a failed move) but whose archive file
  is missing is re-ingested from the sources. It runs on every ingest (it's one `is_file()` per mission), so no separate startup phase, and it checks
  existence, not the checksum, to keep a tick cheap with thousands of kept files. If the sources are gone too, nothing can be done and `reprocess`
  reports the mission as failed ("archive missing or changed"). `watch` does not disable it.

##### Lock (`lock.py`)

- **OS file lock, not a PID check**: `<data dir>/writer.lock` is locked with `msvcrt.locking` (Windows) / `fcntl.flock` (Linux, macOS). The OS drops
  the lock when the holder dies, so a stale lock can't happen; the file's JSON (PID, host, command, since) only feeds the "who holds it" message and a
  "replacing stale lock" warning. Why: PID-liveness checks are racy and PID reuse breaks them. The lock byte on Windows is far past the content so
  the info stays readable.
- **A second writer exits at once** with exit code 3 and the message naming the holder (`--wait SECONDS` waits instead). `watch` takes the lock
  **per tick**, not for its lifetime, so an admin can run `reprocess` / `rebuild-aggregates` while `watch` is up (the tick is skipped and logged).
  Where: `lock.WriterLock`, `watch.watch`.

##### Runner, reprocess, CLI

- **Pipeline injection**: `runner.Pipeline` (group, parse, replay, save, resolve_start); `default_pipeline(cfg)` wires the real ones and loads the
  catalog once per process, lazily. Tests inject fakes.
- **One `IngestRun` row per attempt that did work**, `ok` or `failed`, never for skipped missions. Failed runs keep the archive path/sha when the
  archive step succeeded, the traceback in `error`, and the parse counters seen so far. The OK run is saved inside the same `transaction.atomic()`
  as `save_mission`, so a mission and its run commit together.
- **Parse reads the archive**, not the originals (the archive is the source of truth, doc 04). `ParseStats` counters are filled while `replay`
  consumes the generator, and copied to the run after it.
- **TD-15 hint**: for raw parts the `[0]` part's mtime is passed to `resolve_mission_start`; for imports no hint (the zip entry time is in an unknown
  time zone). `warnings` from it are stored on the run.
- **Migrations (FR-OPS-3)**: every writer command (`ingest`, `watch`, `reprocess`, `rebuild-aggregates`) applies pending migrations at start, under the
  writer lock, before doing anything. `watch` checks once at start, not per tick.
- **Exit codes**: 0 ok; 1 done but some missions failed (or `reprocess --mission` named a mission with no archive); 2 usage/config error; 3 lock held.
- **`il2ks reprocess`**: missions come from the newest archive-writing `IngestRun` of each mission (so failed missions with an archive can be forced),
  **plus archives in `<data dir>/archive/` that have no `IngestRun` at all** (rebuilding from a lost DB, FR-ING-9; these get `fingerprint = ""` so a
  later `ingest` of the log folder sees them as changed and redoes them once). Workers (`ProcessPoolExecutor`, default CPUs-1, below-normal priority
  via `SetPriorityClass` / `os.nice(10)`) import only the core, no Django. The main process is the only writer, with a bounded window of
  `2 x workers` results in flight. A reprocess failure never schedules a retry (`next_retry_at = NULL`) and leaves the previous good rows in place.
  `rebuild_aggregates()` runs once at the end, only if something succeeded.
- **`IngestRun.fingerprint` of a reprocess run** is copied from the run it reprocesses, so discovery doesn't see a change.
- **`ingest --from <dir|file>`**: groups with `txt_as="archive"` (a lone `[0].txt` is a whole mission), treats every mission as complete, keeps sources
  as-is, and is idempotent through the same fingerprint (name, size, mtime), so re-importing the same folder skips everything.
- **Windows tests of the lock** use real child processes (one killed mid-lock); Linux `fcntl` path is untested here.

##### Needs from other areas / not done

- `Mission.completed_cleanly` comes from `MissionResult.mission.completed_cleanly` (AType 7 seen) via persist; ingest doesn't pass the completeness
  reason (`idle` vs `mission_end`) anywhere. If the admin page should show "completed only by idle timeout", `IngestRun` needs a field for it
  (not added: no model changes in this area).
- No `il2ks.toml` template or `il2ks setup` (planned command stubs remain).

