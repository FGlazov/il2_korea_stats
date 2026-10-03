# 06 — Data Model

Status: `[PROPOSED]` unless marked otherwise. Checked against real Korea logs ([12_korea_log_format.md](12_korea_log_format.md)).
Principles (all `[DECIDED]`):
- **Pre-aggregated read models** shaped for the pages. The archived raw logs are the source of truth (TD-08, TD-09).
- **Views do simple reads only**: `SELECT … WHERE …` with simple joins, never `GROUP BY` at request time. Simple arithmetic on columns
  (ratios) is fine and gets done at read time. Ratios aren't stored (TD-22).
- Two table levels to start. More levels may appear as pages need them (TD-08).
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
Mission        id, server_uid, mission_uid (unique together), tour → Tour (it2), map/name, file_path,
               started_at (UTC), ended_at, duration_s, game_date, game_type, settings (json),
               completed_cleanly (bool, AType 7 seen), winning_coalition (nullable), is_hidden,
               -- pre-aggregated for list/detail pages (pilot sorties only; REDFOR/BLUFOR by country code, doc 14):
               players_total (any role), sorties_total, redfor_sorties, blufor_sorties, kills_air, kills_ground
PlayerSortie   id, mission, player → Player, account_uuid + spawn_tick (natural key), name_at_time, profile_uuid,
               aircraft → GameObject, payload_name, coalition, country, role (pilot / gunner),
               spawned_at, took_off_at, landed_at, ended_at, spawn position, flight_time_s, takeoffs, landings,
               air_start (bool), spawn_type (air/runway/parking),
               outcome (landed/ditched/crashed/shot_down/in_flight/not_taken_off/mission_ended/unknown),
               pilot_fate (in_aircraft/bailed_out/exited_on_ground/mission_ended/disconnected/unknown), pilot_fate_source (event/inferred/unknown),
               pilot_status (healthy/wounded/dead/captured), suspected_early_bailout (bool)   -- FR-ING-14 rule v2
               aircraft_status (unharmed/damaged/destroyed), damage_taken (0..1), disconnected (bool),
               loss_cause (attacker/self/none), suspected_structural_failure (bool)          -- FR-ING-17
               is_death, is_plane_lost, is_captured (bool: rules resolved once in replay, level 2 only sums them, doc 13)
               kills_air, kills_ground, assists, friendly_kills, friendly_hits, friendly_damage  -- FR-ING-23
               ammo (json: loaded, left, hits per ammo type; FR-WEB-18 attribution in it1.x),
               damage_breakdown (json: dealt/taken per counterpart),
               timeline (json: ordered key events with time, type, detail, position)   -- positions only on key events, no track
Kill           id, mission, time, killer_sortie → PlayerSortie, victim_sortie → PlayerSortie, is_friendly,
               credit (kill/assist), via (direct / abandoned_aircraft / disconnect), pos_x, pos_y, pos_z
               -- unique (victim_sortie, killer_sortie) [DECIDED 2026-10-03]: a sortie is lost once; a killer gets the kill or an assist.
               -- **PvP only** [DECIDED 2026-10-02]: both killer and victim are player sorties. Kills of or by AI, and AI vs AI,
               --    get no Kill rows. Player kills of AI and ground targets are counters on PlayerSortie and entries in its timeline.
PlayerMission  player, mission, coalition, + counters     -- only for players with a pilot sortie in the mission
```

**Counters** `[DECIDED]` (2026-10-03), one list shared by `PlayerMission`, `Player`, `PlayerAircraft` (and `PlayerTour` in it2): sorties, flight
time, air kills, ground kills, assists, deaths, planes lost, bailouts, suspected early bailouts, captures, takeoffs, landings, friendly kills,
friendly hits, friendly damage. Only **pilot** sorties are counted; gunner sorties are recorded (`role = gunner`) but get no counters or dedicated
pages until gunner stats exist (FR-WEB-14).

## Level 2: cross-mission tables (incremental at ingest, rebuildable from level 1)

```
Player         id, account_uuid (unique), current_name, name_lower (indexed, for search),
               first_seen, last_seen, is_hidden, + all-time counters
PlayerName     player, name, name_lower, first_seen, last_seen
PlayerAircraft player, aircraft, + all-time counters                                     -- per-aircraft table on profile
-- iteration 2 (TD-26):
Tour           id, title, started_at, ended_at (null = current), mode snapshot
PlayerTour     player, tour, + same counters as PlayerMission
(PlayerAircraft gains a tour column in it2)
```
**Only counters are stored.** Ratios (K/D = kills / deaths, K/L = kills / planes lost, kills per sortie, kills per flight hour, survival rate)
are computed at read time from those counters, as model properties or template filters, with divide-by-zero handled (TD-22).

## Reference and operational tables

```
GameObject     id, log_name (unique), display_name, cls (fighter/attacker/bomber/transport/gunner/tank/vehicle/aaa/ship/static/
               ordnance/crew/equipment/unknown), is_playable, is_known (false = auto-registered unknown, FR-ING-7)
Country        code (501...), display_name, coalition                                  -- admin-editable
IngestRun      id, mission_uid, files (json), fingerprint, archive_path, archive_sha256, status (ok/failed/skipped),
               completion_reason (mission_end/newer_mission/idle/import/reprocess), attempts, next_retry_at, started_at, finished_at,
               lines_total, lines_bad, log_version, unknown_atypes (json), unknown_keys (json), warnings (json), error
SiteSettings   singleton: title, server name, logo (path in media/, re-encoded raster), accent colors, description, links  -- TD-25
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
