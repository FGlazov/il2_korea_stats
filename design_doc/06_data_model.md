# 06 — Data Model (preliminary)

Status: `[PROPOSED]` throughout. Partly checked against real Korea logs (see [12_korea_log_format.md](12_korea_log_format.md)).
Must work identically on SQLite and Postgres (TD-04, TD-19). `jsonb` below means Django `JSONField`.

## Identity rules

- **Player identity.** The BoS logs carried two UUIDs per spawn: `LOGIN` (account) and `IDS` (profile/nickname ID).
  One account can own several in-game nicknames (profiles). The old system keyed `Profile` on the **account**
  UUID and stored the current nickname. For Korea:
  - `Player` = one row per account UUID (or profile UUID; see OQ-6), with the current nickname.
  - `PlayerName` = history of nicknames seen, with first and last seen dates. This supports search by an old name.
- **Mission identity**: `(server_id, mission_uid)`, where `mission_uid` is the file name timestamp
  (`2026-09-19_22-34-13`). Korea's AType 0 has an empty `MID:` field, so the game provides no mission ID.
- **In-mission object IDs** are integers that are only unique within one mission. AType 12 can re-declare an existing ID
  (Korea does this for player aircraft and pilots at sortie end). Treat that as an update, not a new object.
- Internal integer PKs are used in URLs for brevity. Game UUIDs are kept for cross-server merging (TD-17).

## Core facts (written by the ingester)

```
Server        id, server_uid (uuid), name
Mission       id, server → Server, mission_uid, map/name, file_path, started_at (UTC), ended_at,
              duration_s, game_date, game_type, settings (jsonb), completed_cleanly (bool),
              winning_coalition (nullable), is_hidden
Player        id, account_uuid (unique), current_name, first_seen, last_seen, is_hidden
PlayerName    player → Player, name, first_seen, last_seen
GameObject    id, log_name (unique), display_name, cls (fighter/bomber/attacker/tank/aaa/ship/...),
              is_playable, is_known (false = auto-created unknown, FR-ING-7)
Sortie        id, mission, player, aircraft → GameObject, coalition, country, role (pilot/gunner/...),
              spawned_at, took_off_at, landed_at, ended_at, flight_time_s, air_start (bool),
              outcome (landed/ditched/crashed/shot_down/in_flight/not_taken_off/...),
              pilot_status (healthy/wounded/dead/captured/bailed_out/...),
              bailout_source (event/inferred/unknown)   -- no AType 18 for player pilots in Korea (FR-ING-14)
              aircraft_status (unharmed/damaged/destroyed),
              ammo_used (jsonb: bullets/shells/bombs/rockets), damage_taken_pct, disconnected (bool)
Kill          id, mission, tick/time, attacker_sortie (nullable: AI), attacker_object,
              victim_sortie (nullable: AI/ground), victim_object, is_friendly, credit (kill/assist/shared), pos (x,y,z)
Damage        mission, attacker_sortie, victim_sortie|victim_object, total_damage, hits    -- aggregated per pair (TD-08)
SortieEvent   sortie, time, type (spawn/takeoff/hit/damaged/kill/bailout/land/end/...), detail (jsonb)
              -- powers the sortie timeline (FR-WEB-6)
```

## Operational tables

```
IngestRun     id, mission_uid, files (jsonb), status (ok/failed/skipped), started_at, finished_at,
              lines_total, lines_bad, warnings (jsonb), error (text)
SiteSettings  singleton: site title, server name, description, links             (FR-ADM-2)
```

## Derived / aggregate tables (rebuildable, TD-08)

v1 probably needs none: compute player totals with SQL. Add these when a page needs them:
```
PlayerTotals          player, [period/tour], sorties, flight_time, kills_air, kills_ground, deaths, ...
PlayerAircraftTotals  player, aircraft, ...        (later: aircraft stats, from mod_stats_by_aircraft ideas)
```

## Open modeling questions
- Do gunners and other crew get their own sorties (the old system split pilots, gunners, and tankers)? (OQ-4)
- Tours or campaigns: do we need them at all, and how are they defined (by month, manually, by mission win)? (OQ-4)
- Do we store positions for the sortie map, and at what resolution? (OQ-14)
- Kill-credit policy: port the `il2_stats` rules (TD-21, decided). It lives in `core.replay` rules.
- AType 1 hits are 97% `explosion` (about 61k per mission). Store only aggregated hit counts per (sortie, ammo type), not individual rows.
