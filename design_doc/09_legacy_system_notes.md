# 09 — Legacy System Notes (`il2_stats`)

Reference analysis of the old system at `../../il2_stats` (relative to this folder), written so future sessions
don't have to re-derive it. **Everything here describes the BoS-era system and log format. Verify each
point against IL-2 Korea logs before relying on it.**

**Why it fails on Korea logs** (verified by running its replay code on the samples): see
[12_korea_log_format.md](12_korea_log_format.md#why-il2_stats-fails-on-korea-logs-verified-by-running-its-replay-on-20-sample-missions).
In short: a `KeyError` on unknown objects aborts every mission. When that's patched, outcomes are still wrong, because
AType 12 re-declares player objects and AType 18 is missing for player pilots. New ATypes 24–31 get dropped.

## Stack and layout
- Python 3.5, Django 1.11, Postgres 9.5 (needs the `hstore` and `citext` extensions), waitress, Pillow 6, psycopg2.
- Config lives in `src/conf.ini` (loaded by `src/config.py`). Windows `.cmd` / Linux `.sh` scripts in `run/`.
- History: started in 2015 by =FB=Vaal and =FB=Isay. Upstream commits continue into 2025, mostly adding new
  objects and countries. MIT license.

| Path | Role |
|---|---|
| `src/mission_report/parse_mission_log_line.py` | One regex per `AType` (0–22). `parse(line) -> dict`. Field converters in `params_handlers` |
| `src/mission_report/report.py` | `MissionReport`: an in-memory replay state machine (`Object`, `Sortie`, `Area`, `Airfield`). ~900 lines |
| `src/mission_report/statuses.py` | Sortie, life, and bot status state machines |
| `src/mission_report/constants.py` | Countries → coalitions, game class prefixes. **Hard-codes 2 coalitions** |
| `src/mission_report/tests/` | The only real test suite (parser, report, statuses) |
| `src/stats/stats_whore.py` | The loader: polling loop, file grouping, backup, and `stats_whore()`, which writes a mission and updates all aggregates in one big transaction |
| `src/stats/models.py` | ~1,600 lines. `Player` has ~100 denormalized counter columns per tour |
| `src/objects.csv`, `score.csv`, `classes.csv` | Object catalog (~3,300 log names → class), score per class |
| `src/stats/views.py` + templates | SSR pages: missions, mission, pilot(s), sorties, sortie, sortie log, vlifes, killboard, awards, squads, tours, online |
| `src/squads/`, `src/users/`, `src/chunks/` | Squads, user registration (needed email/SMTP), editable text snippets |

## Log format summary (BoS era; for Korea's actual format see doc 12)
- Files: `missionReport(YYYY-MM-DD_HH-MM-SS)[N].txt`. A mission is split across files `[0]..[N]`. The timestamp is the
  server's local time and acts as the mission key.
- One event per line: `T:<tick> AType:<n> key:value ...`. **50 ticks = 1 s.**
- Event types: 0 mission start (map file, game date, country→coalition map, settings), 1 hit (ammo, attacker, target),
  2 damage, 3 kill, 4 sortie end (ammo left), 5 takeoff, 6 landing, 7 mission end, 8 mission objective result,
  9 airfield, 10 player spawn (account UUID, profile UUID, nickname, aircraft, payload, fuel, skin, weapon mods),
  11 group, 12 object spawn, 13 influence area, 14 area boundary, 15 log version, 16 bot deinit, 17 position,
  18 bailout/eject, 19 round end, 20 player connect, 21 player disconnect, 22 tank movement.
- Objects are referenced by integer IDs that are only unique **within a mission**. Players are "bots" inside
  aircraft objects (`PID` vs `PLID`), with parent links for turrets and gunners.
- Sometimes events reference objects that were never spawned, and the report creates them lazily from sortie data.
- Text logging had to be enabled in DServer `startup.cfg` (`mission_text_log = 1`).

## Mission completion detection (old)
- The loader processes every mission except the newest right away. The newest one gets processed once its last file is
  **more than 2 minutes old**. This is a heuristic that guesses when the mission ended.
- Already-processed missions get skipped by checking `Mission.timestamp` in the DB.
- The "online now" data comes from reading the in-progress mission's files.

## Rules worth keeping (as a starting point for `core.replay`)
- Sortie outcome state machine: not_takeoff → in_flight → landed/ditched/crashed/shotdown.
- Landing location checks (friendly airfield vs enemy territory, using influence areas) decide whether a pilot was
  captured, or whether a landing counts as "returned to base".
- Kill credit: kills get attributed using damage accumulated per attacker. "Killed by damage" thresholds, and
  assists for other damagers.
- The README warns that the stats **deliberately differ from in-game stats** and assume the server setting `finishMissionIfLanded`.

## Pain points observed in the code (avoid these)
1. **Monolithic loader.** `stats_whore()` mixes parsing orchestration, persistence, and every aggregate update.
   It's hard to test, so it isn't tested.
2. **Incremental denormalized counters.** `Player`, `PlayerMission`, `PlayerAircraft`, and `VLife` are updated
   incrementally. Fixing a bug requires "retro compute" background jobs and data-fix migrations
   (see `mod_stats_by_aircraft/background_jobs/fix_*.py` and the `000X_fix_*` migrations). → TD-08.
3. **Fragile on unknown objects.** Persistence looks up `objects[sortie.aircraft_name]['id']`, so an aircraft missing
   from `objects.csv` appears to abort the mission. That's an issue after every game patch. → FR-ING-7.
4. **Import-time globals.** Modules read `settings.X` at import, which makes things hard to test or reconfigure. → TD-11.
5. **Install complexity** (see 07) and **outdated pins** (Python 3.5-compatible code, pinned pip and setuptools).
6. **Two coalitions hard-coded.** Fine for Korea (UN vs Communist, presumably), but it should be explicit.
7. Mixed Russian and English comments. Low type-hint coverage.

## The maintainer's mods: `mod_rating_by_type`, `mod_stats_by_aircraft`
Written by the maintainer on top of `il2_stats` (their git history isn't included in the local copy).
**These are the features to bring over after the PoC.** They're mechanically a gotcha: both mods install
themselves in `AppConfig.ready()` by **monkeypatching** the core. They replace `stats.urls.urlpatterns`,
about 30 view functions, `MissionReport.event_*` and `Object.got_*` methods, loader functions
(`create_new_sortie`, `update_*`, `main`), `import_csv_data`, model methods, and reward functions. They also append
middleware and inject config defaults. Any change to the core can break them silently. → TD-16 defines
real extension points instead.

Feature inventory (from `mod_rating_by_type/config_modules.py` and the models), all toggleable in `conf.ini`:
- **Split rankings by aircraft class** (light/medium/heavy): separate score, rating, streaks, and profile pages per class.
- **Stats by aircraft** (`mod_stats_by_aircraft`): per-aircraft and per-pilot-per-aircraft stats, aircraft killboards,
  streaks, and pilot rankings per aircraft.
- **Ammo breakdown**: which ammo or weapons caused hits and kills (uses AType 1 hit data).
- **Ironman stats**: "virtual life" based rankings (stats over one life, without dying). Includes last-mission ironman and squad ironman.
- **Gunner stats**: separate gunner profiles, sorties, and rankings.
- **Adjustable bonuses and penalties**, undamaged bailout penalty, flight time bonus, no parachute deaths, rams.
- **Accuracy workarounds** for rearming and bailouts. Air streaks that ignore AI kills.
- Tour handling (new tour on mission win), "top last mission", ITAF layout (server-specific layout).

In the new design, most of these map to: **replay rules** (penalties, rams, parachute deaths, accuracy fixes),
**aggregators** (split rankings, per-aircraft stats, ironman, gunner stats), and **web feature modules**.
They depend on storing enough fact detail (hits by ammo type, per-life grouping), which feeds into OQ-14.
