# 06 — Data Model

Status: `[PROPOSED]` unless marked otherwise. Checked against real Korea logs ([12_korea_log_format.md](12_korea_log_format.md)).
Principles (all `[DECIDED]`):
- **Pre-aggregated read models** shaped for the pages. The archived raw logs are the source of truth (TD-08, TD-09).
- **Views do simple reads only**: `SELECT … WHERE …` with simple joins, never `GROUP BY` at request time. Simple arithmetic on columns
  (ratios) is fine and gets done at read time. Ratios aren't stored (TD-22). **One documented exception:** `AircraftStats` stores four ratio
  fractions (`kd`, `kl`, `survival`, `attack_share`) so the aircraft list can sort by them without computing at request time `[PROPOSED]`.
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
               completed_cleanly (bool, AType 7 seen), winning_coalition (nullable), is_hidden,
               -- pre-aggregated for list/detail pages (pilot sorties only; REDFOR/BLUFOR by country code, doc 14):
               players_total (any role), sorties_total, redfor_sorties, blufor_sorties, kills_air, kills_ground, friendly_kills
PlayerSortie   id, mission, player → Player, account_uuid + spawn_tick (natural key), name_at_time, profile_uuid,
               aircraft → GameObject, payload_name, coalition, country, role (pilot / gunner),
               spawned_at, took_off_at, landed_at, ended_at, spawn position, flight_time_s, takeoffs, landings,
               air_start (bool), spawn_type (air/runway/parking),
               outcome (landed/ditched/crashed/shot_down/airborne/in_flight/not_taken_off/unknown), ended_by_mission_end (bool),
               pilot_fate (in_aircraft/bailed_out/exited_on_ground/disconnected/unknown), pilot_fate_source (event/inferred/unknown),
               pilot_status (healthy/wounded/dead/captured), suspected_early_bailout (bool)   -- FR-ING-14 rule v2
               aircraft_status (unharmed/damaged/destroyed), damage_taken (0..1), disconnected (bool),
               loss_cause (attacker/self/none), suspected_structural_failure (bool)          -- FR-ING-17
               taxi_accident, strafed_on_ground (bool: aircraft lost on the ground, doc 13)
               combat_role (air_superiority/attack; null for gunners), time_on_target_s (null unless attack)  -- FR-WEB-19/20
               is_death, is_plane_lost, is_captured (bool: rules resolved once in replay, level 2 only sums them, doc 13)
               loss_class (who is behind the loss; '' = nothing lost), kills_air_pvp, kills_air_ai   -- FR-WEB-21, doc 13
               kills_ground_<category> (9 categories) + kills_ground_static                           -- they sum to kills_ground (OQ-33)
               air_points, ground_points (float)   -- the sortie's score under the `[score]` rules (FR-WEB-7, doc 13); a changed rule
                                                   -- applies with `rebuild-aggregates`, no reprocess
               kills_air, kills_ground, assists, friendly_kills, friendly_hits, friendly_damage  -- FR-ING-23
               resupplied (bool, FR-ING-24), ammo (json: loaded, left, used, hits per ammo type; FR-WEB-18 attribution in it1.x),
               damage_breakdown (json: dealt/taken per counterpart),
               timeline (json: ordered key events with time, type, detail, position)   -- positions only on key events, no track
Kill           id, mission, tick, time, killer_sortie → PlayerSortie, victim_sortie → PlayerSortie, is_friendly,
               credit (kill/assist), via (direct / abandoned_aircraft / disconnect), pos_x, pos_y, pos_z
               -- unique (victim_sortie, killer_sortie) [DECIDED 2026-10-03]: a sortie is lost once; a killer gets the kill or an assist.
               -- **PvP only** [DECIDED 2026-10-02]: both killer and victim are player sorties. Kills of or by AI, and AI vs AI,
               --    get no Kill rows. Player kills of AI and ground targets are counters on PlayerSortie and entries in its timeline.
PlayerMission  player, mission, coalition, + counters     -- only for players with a pilot sortie in the mission
MissionAircraftAmmo  mission, aircraft, ammo, kills, hits  -- FR-WEB-18: gun hits that destroyed aircraft of a type in one mission;
                                                           -- `TOTAL_AMMO` = all gun ammo together
```

**Counters** `[DECIDED]` (2026-10-03), one list shared by `PlayerMission`, `Player`, `PlayerAircraft`, `PlayerTour`, `PlayerTourAircraft`,
`PlayerPool`, `PlayerTourPool` and `AircraftStats`: sorties, flight time, air kills, ground kills, assists, deaths, planes lost, bailouts,
suspected early bailouts, captures, takeoffs, landings, friendly kills, friendly hits, friendly damage, **taxi accidents, strafed on the
ground, attack sorties, time on target** (2026-10-03, doc 13). Added since, all sums of sortie columns: **`friendly_fire_incidents`**
(sorties with at least one friendly kill; hits and damage alone don't count, OQ-72), the ground-kill categories and `kills_ground_static`
(OQ-33), the PvE families `kills_air_pvp` / `kills_air_ai`, `deaths_by_<class>` and `planes_lost_by_<class>` (8 classes each, FR-WEB-21), and
**`score_air`, `score_ground`, `score_ground_attack`** (sums of the sorties' `air_points` / `ground_points`; the last is the part earned in
attack sorties, which divided by `time_on_target_s` gives the ground proficiency, FR-WEB-20). Only
**pilot** sorties are counted; gunner sorties are recorded (`role = gunner`) but get no counters or dedicated
pages until gunner stats exist (FR-WEB-14).

## Level 2: cross-mission tables (recomputed at ingest, rebuildable from level 1)

All of it is rebuilt by `il2ks rebuild-aggregates` and kept equal to it by the incremental updates (tests compare both). Hidden players stay in
every aggregate: hiding is presentation only (FR-ADM-3).

```
Player         id, account_uuid (unique), current_name, name_lower (indexed, for search),
               first_seen, last_seen, is_hidden, + all-time counters,
               elo_prop, elo_jet (float), elo_prop_games, elo_jet_games (int)   -- FR-WEB-19, not counters: a global replay (doc 13)
PlayerName     player, name, name_lower, first_seen, last_seen
PlayerAircraft player, aircraft, + all-time counters, elo, elo_games    -- per-aircraft table on profile; the Elo is per (player, type) from
                                                                           -- the same replay (OQ-49), written by `recompute_ratings`, not a sum
Tour           id, title, started_at, ended_at (null = current), mode snapshot   -- TD-26
PlayerTour     player, tour, + same counters as PlayerMission
PlayerTourAircraft player, tour, aircraft, + counters   -- a separate table, so all-time PlayerAircraft reads stay untouched
PlayerPool     player, propulsion (prop/jet), + counters         -- counters of the sorties in prop or jet aircraft: the leaderboards'
PlayerTourPool player, tour, propulsion, + counters              -- `?pool=` filter; unknown propulsion is in no pool
Mission.tour   FK (assigned at ingest by started_at in tours.timezone)

-- statistics, boards, activity (FR-WEB-7, 8, 9, 16, 22, 25):
StatThreshold  tour (null = all time), metric, min_sorties, population, p10, p25, p50, p75, p90
               -- FR-WEB-22: percentiles of one ratio over pilots with >= `[marks] min_sorties`; no row = too few pilots for a distribution
AircraftStats  aircraft (1:1 → GameObject), pilots, side (redfor/blufor/''), + counters, kd, kl, survival, attack_share
               -- FR-WEB-8: all-time sum of the type's PlayerAircraft rows. The four ratios are fractions stored only so the list can sort
               -- by them (the documented exception to "ratios aren't stored"; 0.0 where the denominator is 0, the page shows a dash)
AircraftMatchup killer_aircraft, victim_aircraft, kills     -- PvP kill credits type vs type; a type's losses are the reversed pair
AircraftPayload aircraft, payload_name ('' = unnamed), sorties, kills_air, kills_ground, deaths
AircraftAmmoStats aircraft, ammo, kills, hits               -- FR-WEB-18: sum of MissionAircraftAmmo; average hits to destroy = hits / kills
PlayerKillboard player, opponent, kills, deaths, assists, last_at, last_mission
               -- FR-WEB-9: two mirror rows per pair (one per perspective), so a board is one indexed read; `assists` is 0 unless
               -- `[killboard] assists` is on; a pair with only assists has kills = deaths = 0
PlayerTourKillboard player, opponent, tour, kills, deaths, assists, last_at, last_mission   -- the same within one tour
PlayerStreak   player (1:1), current_* and best_* (sorties, kills_air, flight_time_s, since, until)   -- FR-WEB-25: ironman streaks, all time
PlayerBestStreak player, tour (null = all time), kind (sorties / air_kills / flight_time), sorties, kills_air, flight_time_s, since, until
               -- the best streak by each criterion; a tour streak counts only that tour's sorties; an air_kills row exists only when the
               -- best such streak has an air kill
ActivityDay    day (UTC, unique), missions, sorties, pilots, kills_air   -- FR-WEB-16: the home chart; visible missions only, by start time
```
**Only counters are stored.** Ratios (K/D = kills / deaths, K/L = kills / planes lost, kills per sortie, kills per flight hour, survival rate)
are computed at read time from those counters, as model properties or template filters, with divide-by-zero handled (TD-22).

## Reference and operational tables

```
GameObject     id, log_name (unique), display_name, cls (fighter/attacker/bomber/transport/gunner/tank/vehicle/aaa/ship/static/
               ordnance/crew/equipment/unknown), propulsion (prop/jet/blank; aircraft only), is_playable,
               is_known (false = auto-registered unknown, FR-ING-7)
Country        code (501...), display_name, coalition                                  -- admin-editable
IngestRun      id, mission_uid, files (json), fingerprint, archive_path, archive_sha256, status (ok/failed/skipped),
               completion_reason (mission_end/newer_mission/idle/import/reprocess), attempts, next_retry_at, started_at, finished_at,
               lines_total, lines_bad, log_version, unknown_atypes (json), unknown_keys (json), warnings (json), error
SiteSettings   singleton (TD-25): site_title, server_name, description, logo (path in media/, re-encoded raster), coalition names and emblems,
               theme (json: colour overrides per mode, validated #RRGGBB only), heading_font, body_font (keys), links (json: the published copy
               of the NavLink rows, so pages need no extra query),
               killboard_assists (bool; not branding: the `[killboard] assists` value the level-2 rows were last rebuilt with)
NavLink        site, label, url (http/https only), icon (built-in key or none), position   -- ordered inline of SiteSettings, at most 30
DataVersion    singleton counter bumped with every page-visible change (TD-28)
ReprocessRequest, LiveMission, LivePlayer   -- admin reprocess queue (doc 14) and the "online now" snapshot (FR-ING-12, outside the data version)
```

## Page → table map (every page is simple reads)

| Page | Reads |
|---|---|
| Mission list | `Mission` |
| Mission detail | `Mission`, `PlayerSortie` where mission (+ `Player`, `GameObject`) |
| Player profile | `Player` (all-time counters; ratios computed from them), `PlayerAircraft`, recent `PlayerSortie`. In it2: `PlayerTour` for the selected tour |
| Player sorties | `PlayerSortie` where player [and aircraft; tour in it2] |
| Sortie detail | one `PlayerSortie` row (timeline, damage, ammo are json on it; AI and ground kills come from the timeline), `Kill` where killer or victim = sortie (PvP) |
| Player search | `PlayerName` where `name_lower` contains query |
| Home | `Mission` (latest; the last mission's top pilots via `PlayerSortie`), `ActivityDay`, `PlayerStreak`, top 5 of Elo jet, Elo prop and ground per hour (`Player`), `LiveMission` / `LivePlayer` |
| Leaderboards | `Player` / `PlayerTour` (+ `PlayerPool` / `PlayerTourPool` for `?pool=`, `PlayerAircraft` / `PlayerTourAircraft` for `?aircraft=`; Elo boards from `Player.elo_*` or `PlayerAircraft.elo`), `Tour` |
| Aircraft list | `AircraftStats` + `GameObject` |
| Aircraft detail | `AircraftStats`, `AircraftMatchup`, `AircraftPayload`, `AircraftAmmoStats`, top pilots from `PlayerAircraft` |
| Killboard | `PlayerKillboard` / `PlayerTourKillboard` where player; `SiteSettings.killboard_assists` |
| Streaks | `PlayerStreak` (running streaks, `/streaks/`); a player's best streaks: `PlayerBestStreak` (`/players/<id>/streaks/`) |
| Profile extras | `StatThreshold` (highlights), `PlayerStreak`, `PlayerKillboard` top rows, `PlayerTour` (charts) |
| Every page | `SiteSettings` (incl. `links`, `theme`) and `DataVersion` (context processor, caching middleware) |
