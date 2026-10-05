# 06 — Data Model

Status: `[PROPOSED]` unless marked otherwise. Checked against real Korea logs ([12_korea_log_format.md](12_korea_log_format.md)).
Principles (all `[DECIDED]`):
- **Pre-aggregated read models** shaped for the pages. The archived raw logs are the source of truth (TD-08, TD-09).
- **Views do simple reads only**: `SELECT … WHERE …` with simple joins, never `GROUP BY` at request time. Simple arithmetic on columns
  (ratios) is fine and gets done at read time. Ratios aren't stored (TD-22), **without exception** since the maintainer's answer to OQ-98
  (2026-10-04): `AircraftStats` had stored four ratio fractions so the list could sort by them; they are gone (migration 0032), and lists sort by
  an expression of the counters (`queries.sorting.Ratio`, NULL while the denominator is 0, always last).
- Two table levels, plus level-2 tables per tour and per page need (TD-08). Models: `src/il2ks/db/models.py`; the counters are one abstract
  `Counters` model (one registry, `ingest/counters.py`).
- Everything works the same on **SQLite and Postgres** (TD-04, TD-19). `json` below means Django `JSONField`.

## Identity rules

- **Player = game account UUID** (`LOGIN` in AType 10) `[DECIDED]`, like the old system. One person with several
  in-game nicknames is one player. `PlayerName` keeps the nickname history so search finds old names.
  (`IDS`, the profile/nickname UUID, is stored on the sortie for reference.)
- **Mission identity**: `(server_uid, mission_uid)`, where `mission_uid` is the file name timestamp
  (`2026-09-19_22-34-13`). Korea's AType 0 has an empty `MID:` field, so the game provides no mission ID.
- **In-mission object IDs** are integers that are only unique within one mission. AType 12 can re-declare an existing ID
  (Korea does this for player aircraft and pilots at sortie end). Treat that as an update, not a new object.
- Internal integer PKs are used in URLs for brevity. They stay **stable across `reprocess`** because rows are upserted by natural key:
  mission `(server_uid, mission_uid)`, sortie `(mission, account_uuid, spawn_tick)`, player `account_uuid` (FR-ING-9, FR-WEB-13).
  Game UUIDs are kept for future cross-server merging (TD-17).

## Coalitions and countries `[DECIDED]` (generic names for now)
- Display coalition 1 as **REDFOR** (countries 501–503, the communist side) and coalition 2 as **BLUFOR** (601–603, the UN side). We don't
  know which nation each country code is. **All of 501–503 display as plain "REDFOR" and all of 601–603 as plain "BLUFOR"**, with no
  country code in the UI (maintainer, 2026-10-02: "REDFOR 501" is confusing). The code is still stored per sortie, so real nation names can be
  shown later.
- **The side comes from the country code** `[DECIDED]` (maintainer, 2026-10-03): 5xx = REDFOR, 6xx = BLUFOR, whatever coalition number `CNTRS`
  gives it. That's stable even if a mission ever numbers the coalitions differently. Friend or foe still comes from the mission's `CNTRS`.
- Coalition and country display names are admin-editable (FR-ADM-5), so they can be fixed without a release.

## Level 1: mission-level tables (written per mission, rebuildable per mission)

```
Mission        id, server_uid, mission_uid (unique together), tour → Tour, mission_file (map/name), file_path,
               started_at (UTC), ended_at, duration_s, game_date, game_time, game_type, settings (json), countries (json, CNTRS), log_version,
               completed_cleanly (bool, AType 7 seen), winning_coalition (nullable), result (win|draw|unknown, doc 12 "Mission result"), is_hidden,
               is_live (bool, FR-ING-15: provisional rows `watch` saved while the mission still runs; the final save rewrites the same rows by
               their natural keys and clears it) `[PROPOSED]`,
               -- pre-aggregated for list/detail pages (pilot sorties only; REDFOR/BLUFOR by country code, doc 14):
               players_total (any role), sorties_total, redfor_sorties, blufor_sorties, kills_air, kills_ground, friendly_kills
PlayerSortie   id, mission, player → Player, account_uuid + spawn_tick (natural key), name_at_time, profile_uuid,
               aircraft → GameObject, payload_id, payload_name, weapon_mods (int, the log's `WM` bitmask: bit 0 always set, bit k = mod k of `weapon_mods.csv`), coalition, country, role (pilot / gunner),
               spawned_at, took_off_at, landed_at, ended_at, spawn position, flight_time_s, takeoffs, landings,
               air_start (bool), spawn_type (air/runway/parking),
               outcome (landed/ditched/crashed/shot_down/airborne/in_flight/not_taken_off/unknown), ended_by_mission_end (bool),
               pilot_fate (in_aircraft/bailed_out/exited_on_ground/disconnected/unknown), pilot_fate_source (event/inferred/unknown),
               -- (pages show the pilot's fate as Dead / Captured / Survived, derived from is_death / is_captured at display time;
               --  the stored fate stays as the replay wrote it and is only the detail, OQ-106)
               pilot_status (healthy/wounded/dead/captured), suspected_early_bailout (bool)   -- FR-ING-14 rule v2
               aircraft_status (unharmed/damaged/destroyed), damage_taken (0..1; 1.0 when destroyed, OQ-115), disconnected (bool),
               pilot_damage (float, nullable: the pilot's (gunner's) damage, 1.0 when dead, health = 1 - it; NULL = unknown, on sorties from before
               migration 0050 that did not die, until `reprocess --all` fills it; the sortie page shows a gunner's health from it) `[PROPOSED]`,
               loss_cause (attacker/self/none), suspected_structural_failure (bool)          -- FR-ING-17
               taxi_accident, strafed_on_ground (bool: aircraft lost on the ground, doc 13)
               combat_role (air_superiority/attack; null for gunners), time_on_target_s (null unless attack)  -- FR-WEB-19/20
               rams, first_blood (bool), multi_kill, elo_peak   -- achievement facts (doc 17): air kills by ramming, first credited PvP air kill of the mission,
                                                   -- most air kills in one burst window, highest Elo held after a win in the sortie (`ingest.ratings`)
               kills_air_intercept   -- air kills of bombers, attackers and transports (part of kills_air; doc 13 "Interception and tank busting")
               is_death, is_plane_lost, is_captured (bool: rules resolved once in replay, level 2 only sums them, doc 13)
               loss_class (who is behind the loss; '' = nothing lost), kills_air_pvp, kills_air_ai   -- FR-WEB-21, doc 13
               kills_ground_<category> (9 categories) + kills_ground_static                           -- they sum to kills_ground (OQ-33)
               air_points, ground_points (float)   -- the sortie's score under the `[score]` rules (FR-WEB-7, doc 13); a changed rule
                                                   -- applies with `rebuild-aggregates`, no reprocess
               kills_air, kills_ground, assists (= assists_air + assists_ground), assists_air, assists_ground (2026-10-04; only air assists score), friendly_kills, friendly_hits, friendly_damage  -- FR-ING-23
               resupplied (bool, FR-ING-24), rounds_fired (null where unknown: resupplied, unreliable AType 4, gunners), gun_hits_air, gun_hits_ground
               (exact; FR-WEB accuracy, doc 13),
               ammo (json: loaded, left, used, used_estimate {bombs, rockets} (OQ-101: kinds released after a loss, where `used` is unknown), left_after_loss, releases {stores, rocket_salvos}, hits per ammo type,
               ordnance, unattributed; shape in `ingest/persist.py::_ammo_json`, rules in doc 13 "Ammo and resupply"),
               damage_breakdown (json: dealt/taken per counterpart),
               timeline (json: ordered key events with time, type, detail, position)   -- positions only on key events, no track;
                                                   -- hit rows (`hit_given` / `hit_taken`) carry damage, lines, ammo and, unless it is a gun ammo, ammo_kind (doc 14)
Kill           id, mission, tick, time, killer_sortie → PlayerSortie, victim_sortie → PlayerSortie, is_friendly,
               credit (kill/assist), via (direct / abandoned_aircraft / disconnect), pos_x, pos_y, pos_z
               -- unique (victim_sortie, killer_sortie) [DECIDED 2026-10-03]: a sortie is lost once; a killer gets the kill or an assist.
               -- **PvP only** [DECIDED 2026-10-02]: both killer and victim are player sorties. Kills of or by AI, and AI vs AI,
               --    get no Kill rows. Player kills of AI and ground targets are counters on PlayerSortie and entries in its timeline.
PlayerMission  player, mission, coalition, + counters     -- only for players with a pilot sortie in the mission
MissionAircraftAmmo  mission, aircraft, combat_role, weapon_mods, ammo, kills, hits
               -- FR-WEB-18: gun hits that destroyed aircraft of a type in one mission, from kills where all the damage came from one attacker
               -- (`SingleAttackerKill`; any victim and attacker, players or AI); `TOTAL_AMMO` ("*") = all gun ammo together. `combat_role` ('' =
               -- none or not a player) and `weapon_mods` (`NO_MODS_RECORDED` = -1 when the destroyed aircraft was not a counted player sortie:
               -- AI, or a row from before they were stored) are those of the DESTROYED aircraft's own sortie (migration 0057), so the aircraft
               -- page's role and modification scopes can sum them. [PROPOSED]
MissionAircraftAmmoMix  mission, aircraft, combat_role, weapon_mods, mix, ammo, kills, hits
               -- the same kills grouped by the set of gun ammo that hit; mix = sorted ammo log names joined by "|"; one row per member ammo
               -- plus a `TOTAL_AMMO` row; `kills` = instances of the mix (equal on all rows of one mix). Rewritten whenever the mission is saved
```

`SortieGunHits` (one row per pilot sortie and gun ammo) is gone, **dropped in migration 0053**: the ammo mixes come from the single-attacker kills
above, and `PlayerAircraftBuild` keeps only the favourite loadout. `[PROPOSED]` `Kill.credit` also admits `shared` in the schema (a check
constraint), but nothing writes it today; only `kill` and `assist` occur.

**Counters** `[DECIDED]` (2026-10-03), one list shared by `PlayerMission`, `Player`, `PlayerAircraft`, `PlayerTour`, `PlayerTourAircraft`,
`PlayerPool`, `PlayerTourPool`, `PlayerAircraftScope`, `AircraftStats` and `TourAircraftStats`: sorties, flight time, air kills, ground kills, assists (with `assists_air` / `assists_ground`, 2026-10-04), deaths, planes lost, bailouts,
suspected early bailouts, captures, takeoffs, landings, friendly kills, friendly hits, friendly damage, **taxi accidents, strafed on the
ground, attack sorties, time on target** (2026-10-03, doc 13). Added since, all sums of sortie columns: the ground-kill categories and `kills_ground_static`
(OQ-33), the PvE families `kills_air_pvp` / `kills_air_ai`, `deaths_by_<class>` and `planes_lost_by_<class>` (8 classes each, FR-WEB-21), and
**`score_air`, `score_ground`, `score_ground_attack`** (sums of the sorties' `air_points` / `ground_points`; the last is the part earned in
attack sorties, which divided by `time_on_target_s` gives the ground proficiency, FR-WEB-20). For the skill boards (2026-10-04, doc 13):
**`air_superiority_sorties`**, **`flight_time_air_s`** (flight time of air superiority sorties), **`kills_intercept`** (air kills of bombers and
attackers made in air superiority sorties; from `PlayerSortie.kills_air_intercept`) and **`kills_tank_attack`** (tanks destroyed in attack sorties).
Interception per hour = `kills_intercept / flight_time_air_s`, tank busting = `kills_tank_attack / time_on_target_s`, both computed at read time.
For accuracy (2026-10-04, doc 13): **`accuracy_rounds`, `accuracy_hits`** (rounds fired and gun hits of the same sorties, those with a known number of rounds),
**`accuracy_air_rounds`, `accuracy_air_hits`** (air superiority sorties), **`accuracy_ground_rounds`, `accuracy_ground_hits`** (attack sorties) and the
exact **`gun_hits_air`, `gun_hits_ground`** (all sorties; from `PlayerSortie.gun_hits_*`); the ratios are computed at read time. Only
**pilot** sorties are counted; gunner sorties are recorded (`role = gunner`) but get no counters or dedicated
pages until gunner stats exist (FR-WEB-14).

## Level 2: cross-mission tables (recomputed at ingest, rebuildable from level 1)

All of it is rebuilt by `il2ks rebuild-aggregates` and kept equal to it by the incremental updates (tests compare both). The player-side all-time rows (`Player` counters, `PlayerAircraft`, `PlayerPool`, `PlayerAircraftBuild`, `PlayerKillboard`, `PlayerTypeKillboard`, identity) are roll-ups (SUM / MAX / MIN) of their per-tour rows, never computed from level 1 (doc 14). Hidden players stay in
every aggregate: hiding is presentation only (FR-ADM-3).

```
Player         id, account_uuid (unique), current_name, name_lower (indexed, for search),
               first_seen, last_seen, is_hidden, + all-time counters,
               elo_prop, elo_jet (float), elo_prop_games, elo_jet_games (int)   -- FR-WEB-19, not counters: a global replay (doc 13)
PlayerName     player, name, name_lower, first_seen, last_seen   -- MIN / MAX per name over PlayerTourName
PlayerTourName player, tour, name, first_seen, last_seen, last_spawn   -- `[PROPOSED]` identity per tour, from the sorties of all roles; Player.first_seen / last_seen /
                                                                         -- current_name (the name of the latest last_spawn) roll up from it (doc 14)
PlayerAircraft player, aircraft, + all-time counters, elo, elo_games    -- per-aircraft table on profile; the Elo is per (player, type) from
                                                                           -- the same replay (OQ-49), written by `recompute_ratings`, not a sum
Tour           id, title, started_at, ended_at (null = current), mode snapshot, by_win (a part started by a decisive mission)   -- TD-26
PlayerTour     player, tour, + same counters as PlayerMission
PlayerTourAircraft player, tour, aircraft, + counters   -- a separate table, so all-time PlayerAircraft reads stay untouched
PlayerAircraftScope player, aircraft, tour (null = all time), role (all / air_superiority / attack), mod_pattern, + counters
               -- the aircraft page's top pilots in every scope of the page (migration 0057): one player's counters in one type in one tour, combat
               -- role and modification-filter scope (pattern: see `TourAircraftStats`). Every scope has rows except all time + `all` + unfiltered,
               -- which is `PlayerAircraft` itself. Counted from the sorties of each scope, hidden players too (the page leaves them out) [PROPOSED]
PlayerAircraftBuild player, aircraft, tour (null = all time), kind (only `payload`), value, label, sorties
               -- the favourite loadout on the profile: `value` = `payload_id`, `label` = its name ('' = unknown), `sorties` = counted sorties with
               -- it. The kinds `mods` and `ammo` and the `hits` column are gone (OQ-117, migration 0053): mods and ammo are aircraft-page tables
               -- now (`AircraftMods`, `AircraftAmmoStats`). Two partial unique constraints (tour set / null). Recomputed per affected player
PlayerPool     player, propulsion (prop/jet), + counters         -- counters of the sorties in prop or jet aircraft: the leaderboards'
PlayerTourPool player, tour, propulsion, + counters              -- `?pool=` filter; unknown propulsion is in no pool
Mission.tour   FK (assigned at ingest by started_at in tours.timezone)

-- statistics, boards, activity (FR-WEB-7, 8, 9, 16, 22, 25):
StatThreshold  tour (null = all time), metric, min_sorties, population, p10, p25, p50, p75, p90
               -- FR-WEB-22: percentiles of one metric over the pilots who meet that metric's minimum; no row = too few pilots for a distribution.
               -- Metrics: survival, kd, kl, air/ground kills per sortie and hour, taxi/friendly-fire per sortie (never badged), air_score,
               -- ground_score, ground_score_hour, elo_prop, elo_jet (all time only), interception_hour, tank_hour. `min_sorties` holds the
               -- minimum in the metric's unit: sorties (`[marks] min_sorties`), encounters (Elo games), seconds on target or of air superiority
               -- flight (the boards' minimums), so a population follows the board it sits next to
AircraftStats  aircraft (1:1 → GameObject), pilots, side (redfor/blufor/''), + counters
               -- FR-WEB-8: all-time sum of the type's tour rows (`TourAircraftStats`, role all, no mod pattern), pilots = its PlayerAircraft row count; no ratio is stored (OQ-98): K/D, K/L, survival and attack share come
               -- from the counters at read time and sort with `queries.sorting.Ratio`
TourAircraftStats tour (null = all time), aircraft, role (all / air_superiority / attack), mod_pattern, pilots, side, sorties_redfor, sorties_blufor, + counters
               -- AircraftStats within one scope (migration 0057); `sorties_redfor` / `sorties_blufor` (0064) = the row's counted sorties per side, `side` = the larger (ties REDFOR);
               -- a null-tour (all-time) row = the SUM of the tour rows, its side the argmax of the summed counters (doc 14) [PROPOSED]: `role` `all` + no pattern in a tour = sum of the `PlayerTourAircraft` rows; the
               -- role and pattern rows come from the counted sorties of that combat role / modification set (`pilots` = distinct players in that
               -- scope). `tour` null is used only for a role or pattern row (check constraint); the all-time unfiltered `all` row is
               -- `AircraftStats` itself, no duplicate. `mod_pattern` '' = unfiltered; a type with significant weapon mods (`weapon_mods.csv`)
               -- has a row per filter pattern: one character per significant mod in ascending id order, `*` any, `+` with it, `-` without it
               -- (`core.catalog.loader.mod_filter_patterns`), 3**n - 1 more scopes per tour and role; types without significant mods have none.
               -- The aircraft list and detail follow `?tour=` (OQ-114); rows without a counted sortie are deleted [PROPOSED]
AircraftMatchup killer_aircraft, victim_aircraft, tour (null = all time), intercept (bool), scoped_side ('' / killer / victim), combat_role, mod_pattern, kills
               -- PvP kill credits type vs type; a type's losses are the reversed pair. One row per scope: all time or one tour, all kills or only
               -- `intercept` kills (both sorties air superiority), so a kill is counted in up to four rows (FR-WEB-8, OQ-110). Since 0057 also
               -- the role / modification scopes: `scoped_side` '' = unscoped; 'killer' / 'victim' = only kills where THAT side's sortie had
               -- `combat_role` and weapon mods matching `mod_pattern` (of that side's type). The page of type A reads its kills as the killer
               -- side and its losses as the victim side of its own scope: the matchups scope THIS type's sortie only, the opponent is
               -- unrestricted [PROPOSED]
AircraftEffectiveness (abstract)  aircraft, tour (null = all time), combat_role ('' = none), mod_pattern, sorties, kills_air, kills_ground, deaths,
               kills_air_pvp, flight_time_s, score_ground_attack, time_on_target_s, elo_avg (null)
               -- the columns every "effectiveness by X" table of the aircraft page shares (doc 13); `elo_avg` = sortie-weighted average Elo of the
               -- pilots who flew the group (air superiority groups; the pilot's per-type Elo where they have games in the type, else the pool's;
               -- null = no rated pilot), written by `ingest.aircraft_stats.recompute_payload_elo` after `recompute_ratings` [PROPOSED]
AircraftPayload (AircraftEffectiveness) + payload_name ('' = unnamed)   -- sorties per type, loadout, combat role and modification filter
AircraftMods (AircraftEffectiveness) + weapon_mods (the WM bitmask as flown, base bit included)   -- the "Mods" table next to the payload table;
               -- names come from `weapon_mods.csv` at read time
AircraftAmmoStats aircraft, tour (null = all time), role, mod_pattern, ammo, kills, hits
               -- FR-WEB-18: sum of `MissionAircraftAmmo` over the scope's missions; `role` (`all` = every destroyed aircraft, AI included) and
               -- `mod_pattern` are those of the DESTROYED aircraft's sortie; average hits to destroy = hits / kills at read time. The all-time
               -- `all` unfiltered rows are what the aircraft list reads
AircraftAmmoMixStats aircraft, tour (null = all time), role, mod_pattern, mix, ammo, kills, hits   -- the same sum of `MissionAircraftAmmoMix`; the page's ammo mixes (OQ-116)
PlayerKillboard player, opponent, kills, deaths, assists, assists_received, last_at, last_mission
               -- FR-WEB-9: two mirror rows per pair (one per perspective), so a board is one indexed read; `assists` is 0 unless
               -- `[killboard] assists` is on; a pair with only assists has kills = deaths = 0
PlayerTourKillboard player, opponent, tour, kills, deaths, assists, assists_received, last_at, last_mission   -- the same within one tour
PlayerTypeKillboard player, tour (null = all time), enemy_aircraft, kills, deaths, kills_with, deaths_in, kills_with_counts, deaths_in_counts
               -- the two JSON counts (own aircraft type id -> n) are on the tour rows only: the all-time kills_with / deaths_in are the most used type of their sum
               -- FR-WEB-9: the killboard by aircraft type; `kills` = the player's PvP air kill credits on that enemy type, `deaths` = the credits of
               -- pilots flying that type on the player's sorties; `kills_with` / `deaths_in` = the player's own type most used in those fights
               -- (ties: lowest id). Hidden opponents count; no assists. Built by `ingest.type_board`, recomputed per affected player
PlayerStreak   player (1:1), current_tour, current_* and best_* (sorties, kills_air, flight_time_s, since, until)   -- FR-WEB-25: ironman streaks; best = over the tours, current = the run in the newest tour (zero if not flown in it)
PlayerBestStreak player, tour (null = all time), kind (sorties / air_kills / flight_time), sorties, kills_air, flight_time_s, since, until
               -- the best streak by each criterion; a tour row counts only that tour's sorties, the all-time row (tour null) is the best of the tour rows; an air_kills row exists only when the
               -- best such streak has an air kill
PlayerStreakRun player, tour (null = all time), sorties, kills_air, flight_time_s, since, until, ended_by (death / captured / open), ended_sortie → PlayerSortie
               -- FR-WEB-25, OQ-82: every streak of at least `MIN_LISTED_RUN` survived sorties, finished or running; `ended_sortie` is the fatal or
               -- capturing sortie (null while open); `open` also covers a tour that ran out. A tour's runs stay inside the tour. Built by `ingest.streaks`
PlayerAchievement player, tour (null = all time), key, tier, earned_at, sortie, mission   -- FR-WEB-26, doc 17: one row per earned tier, unique (player, tour, key, tier) as two conditional constraints (tour set / null); a tour's rows come from that tour's sorties only
AchievementHolders tour (null = all time), key, tier, holders, pilots   -- visible pilots holding each tier in the scope and the scope's visible pilots with a sortie in it (the rarity denominator), for the overview, the hover text and the home feed (no counting at request time)
ActivityDay    day (UTC, unique), missions, sorties, pilots, kills_air   -- FR-WEB-16: the home chart; visible missions only, by start time
```
**Only counters are stored.** Ratios (K/D = kills / deaths, K/L = kills / planes lost, kills per sortie, kills per flight hour, survival rate)
are computed at read time from those counters, as model properties or template filters, with divide-by-zero handled (TD-22).

## Reference and operational tables

```
GameObject     id, log_name (unique), display_name, name_overridden (bool: an admin edited display_name), cls (fighter/attacker/bomber/transport/gunner/tank/vehicle/aaa/ship/static/
               ordnance/crew/equipment/unknown), propulsion (prop/jet/blank; aircraft only), ground_category (ground-kill category of the type, blank = none), is_playable,
               is_known (false = auto-registered unknown, FR-ING-7)
Country        code (501...), display_name, coalition                                  -- admin-editable
IngestRun      id, mission_uid, files (json), fingerprint, archive_path, archive_sha256, status (ok/failed/skipped),
               completion_reason (mission_end/newer_mission/idle/import/reprocess), attempts, next_retry_at, started_at, finished_at,
               il2ks_version, lines_total, lines_bad, log_version, unknown_atypes (json), unknown_keys (json), warnings (json), error
SiteSettings   singleton (TD-25): site_title, server_name, description, logo (path in media/, re-encoded raster), coalition names and emblems,
               theme (json: colour overrides per mode, validated #RRGGBB only), heading_font, body_font (keys), links (json: the published copy
               of the NavLink rows, so pages need no extra query),
               custom_fonts (json: up to 6 uploaded .woff2/.woff files `{file, label}`; a font is chosen by putting its key `up-<hash8>` in
               heading_font / body_font),
               home_feature (none / image), feature_image_path (the configured file on the server; never served itself), feature_caption,
               feature_alt, and the output of `web.feature_image.sync` (run by the admin save and a polling thread of the web process):
               feature_image, feature_image_small (re-encoded, content-hashed copies under media/), feature_image_width / _height,
               feature_image_updated, feature_source_sig (path, mtime, size of the last file looked at: unchanged = nothing to do), feature_error
               ('' = fine). An `embed` (iframe) mode may come later. FR-ADM-2 [PROPOSED]
               quips_enabled (bool, the global switch), quips (json `{"modes": {spot: mode}, "hidden": {spot: [english default text]},
               "custom": [{spot, text, language, enabled}]}`, parsed and validated by `web.quips`; empty = every default quip on), FR-WEB-23,
               achievements (json `{"off": [key], "thresholds": {key: [n]}, "names": {key: {language: text}}, "descriptions": {...}}`, the admin's
               choice, `web.achievement_config`; empty = the built-in set), achievements_applied (json `{"off", "thresholds"}`: what the stored
               `PlayerAchievement` rows were last computed with, written only by `ingest.achievements`; it differs from `achievements` while
               a recompute is pending), doc 17,
               show_live_sorties (bool, default on: `watch` saves the running mission provisionally and its sorties count, FR-ING-15),
               killboard_assists (bool; not branding: the `[killboard] assists` value the level-2 rows were last rebuilt with),
               backfills_done (json list: the one-time upgrade backfills that already ran, `ops/migrate.py`, FR-OPS-3)
               -- all of it sits on the one settings row every page already reads, so quips, achievement texts and the feature image cost no query
NavLink        site, label, url (http/https only), icon (built-in key or none), position   -- ordered inline of SiteSettings, at most 30
DataVersion    singleton counter bumped with every page-visible change (TD-28)
ReprocessRequest   requested_at/by, since, until (both empty = all), status (pending/running/done/failed), counts, error; at most one pending (partial unique)
LiveMission    server_uid (unique), mission_uid, mission_file, started_at, game_date/time, elapsed_s, updated_at, interval_s, is_running   -- the running
               -- mission as `watch` last saw it, one row per server; `updated_at` older than 3 x `interval_s` = stale
LivePlayer     mission, player (null = unknown account), account_uuid, name, coalition, country, aircraft_type, aircraft_name, propulsion, state
               (in_flight/on_ground/spawned/connected), sortie_started_at, flight_time_s, kills_air, kills_ground   -- replaced wholesale per snapshot
               -- ("online now", FR-ING-12; outside the data version, TD-28)
```

**Indexes (mostly migration 0058, `[PROPOSED]`, maintainer request 2026-10-04 "read-heavy, an index is cheap")**: partial / covering indexes so that
every board and list orders by an indexed column (doc 16 "Query-plan test"): `Player` (one per all-time board: air score, ground score, play time, Elo
jet, Elo prop, plus the player list's last seen and air kills; they carry the minimum-activity filter columns and `is_hidden`), `Mission`
(newest first, all time and per tour), `PlayerBestStreak` (`bests_list`, and a partial `bests_alltime_list` for `tour IS NULL`), `PlayerStreakRun`
(`streakruns_alltime`), `PlayerPool` by propulsion, `PlayerAchievement` (holders by tour/key/tier, the feed by tour/time), `AircraftMatchup` by
killer, `PlayerAircraftScope` by aircraft and scope. Nullable-`tour` tables use **two partial unique constraints** (tour set / tour null), since SQL
treats NULLs as distinct (`scoped_unique` in the models).

## Page → table map (every page is simple reads)

| Page | Reads |
|---|---|
| Mission list | `Mission` |
| Mission detail | `Mission`, `PlayerSortie` where mission (+ `Player`, `GameObject`) |
| Player profile | `Player` (all-time counters; ratios computed from them), `PlayerAircraft`, `PlayerAircraftBuild` (favourite loadout), recent `PlayerSortie`. In it2: `PlayerTour` for the selected tour |
| Player sorties | `PlayerSortie` where player [and aircraft; tour in it2] |
| Sortie detail | one `PlayerSortie` row (timeline, damage, ammo are json on it; AI and ground kills come from the timeline), `Kill` where killer or victim = sortie (PvP) |
| Player search | `PlayerName` where `name_lower` contains query |
| Home | `?tour=` filter (OQ-79; none = current tour, `all` = all time): `Mission` (latest; the last mission's top pilots via `PlayerSortie`), `ActivityDay`, streaks (`PlayerStreak` all time; `PlayerBestStreak` of the tour), top 5 of Elo jet, Elo prop (all time only), interception, ground per hour, tank busting and play time (`Player` / `PlayerTour`, one read each), `LiveMission` / `LivePlayer` (always live) |
| Leaderboards | `Player` / `PlayerTour` (+ `PlayerPool` / `PlayerTourPool` for `?pool=`, `PlayerAircraft` / `PlayerTourAircraft` for `?aircraft=`; Elo boards from `Player.elo_*`; the per-hour skill boards divide two stored counters), `Tour` |
| Aircraft list | `AircraftStats` (`TourAircraftStats` for `?tour=`) + `GameObject` |
| Aircraft detail | `AircraftStats` / `TourAircraftStats` (the chosen tour, role and mods scope), `AircraftMatchup` (the same scope plus intercept), `AircraftPayload`, `AircraftMods`, `AircraftAmmoStats`, `AircraftAmmoMixStats` (all in the scope), top pilots from `PlayerAircraft` (all-time, unfiltered) or `PlayerAircraftScope`, all-time per-type Elo from `PlayerAircraft` |
| Killboard | `PlayerTypeKillboard` (by aircraft type), `PlayerKillboard` / `PlayerTourKillboard` where player; `SiteSettings.killboard_assists` |
| Achievements | `PlayerAchievement` (profile medal row, `/players/<id>/achievements/`, holders page), `AchievementHolders` (`/achievements/`) |
| Streaks | `PlayerBestStreak` of the selected tour or all time (best list) and `PlayerStreak` (running streaks, not on a past tour), `/streaks/`; a player's best streaks: `PlayerBestStreak` (`/players/<id>/streaks/`); all of a player's runs: `PlayerStreakRun` (`/players/<id>/streaks/history/`) |
| Profile extras | `StatThreshold` (highlights), `PlayerStreak`, `PlayerKillboard` and `PlayerTypeKillboard` top rows, `PlayerAchievement`, `PlayerTour` (charts) |
| Every page | `SiteSettings` (incl. `links`, `theme`) and `DataVersion` (context processor, caching middleware) |
