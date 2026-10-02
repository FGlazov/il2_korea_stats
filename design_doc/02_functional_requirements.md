# 02 — Functional Requirements

IDs are stable. Reference them in code comments, tests, and commits (for example `FR-ING-3`).
Priority: **v1** = first iteration (MVP), **later** = see [10_roadmap.md](10_roadmap.md).

## Ingestion (FR-ING)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ING-1 | Find mission log files in a configured DServer log directory. | v1 | `[DECIDED]` |
| FR-ING-2 | Work out when a mission's logs are **complete** (the mission ended and no more files will be written) and only process complete missions. | v1 | `[PROPOSED]`: complete = AType 7 seen, **or** a newer mission's `[0]` file exists, **or** no new part for N minutes. AType 7 alone isn't reliable (4 of 210 samples have none). See [12](12_korea_log_format.md#timing) |
| FR-ING-3 | Parse every log line into a typed event. Unknown or malformed lines get logged and counted, and **never crash ingestion**. | v1 | `[PROPOSED]` |
| FR-ING-4 | Replay a mission's events into a mission result: sorties, outcomes (landed, crashed, shot down, bailed out, and so on), kills and assists, damage dealt and taken, takeoffs and landings, and ammo used. | v1 | `[PROPOSED]` |
| FR-ING-5 | Save one mission atomically, all or nothing, in a single DB transaction. | v1 | `[PROPOSED]` |
| FR-ING-6 | **Idempotent**: processing the same mission twice must not create duplicates. Rerunning the job is always safe. | v1 | `[PROPOSED]` |
| FR-ING-7 | Game objects that aren't in the object catalog (new aircraft, vehicles) get stored as "unknown" and flagged for the admin. Processing continues. | v1 | `[PROPOSED]` |
| FR-ING-8 | Archive raw logs (compressed) after processing, with configurable retention, so missions can be **reprocessed** later. | v1 | `[PROPOSED]` |
| FR-ING-9 | A `reprocess` command rebuilds the DB, or a set of missions, from archived logs after parser or logic fixes. | v1 | `[PROPOSED]` |
| FR-ING-10 | Optionally delete original log files after a successful archive (off by default). | v1 | `[PROPOSED]` |
| FR-ING-11 | Record the result of each ingestion (mission, status, line counts, warnings, duration) so the admin can see it. | v1 | `[PROPOSED]` |
| FR-ING-12 | "Online now": show players currently on the server, read from the in-progress mission's logs. | later | `[OPEN]` OQ-9 |
| FR-ING-13 | **Import concatenated archives**: a single `.txt` or `.txt.zip` that holds a whole mission (the `il2_stats` backup format, and how `sample_data/` is stored). Used to load history and for dev/testing. | v1 | `[PROPOSED]` |
| FR-ING-14 | **Bailout is inferred** for player pilots (the game doesn't log AType 18 for them). Store how it was determined (`event` / `inferred` / `unknown`) so the UI can show it honestly, and switch to the real event if the game fixes it. | v1 | `[PROPOSED]` (rule design OQ-19) |

## Website: players (FR-WEB)

All pages are public and read-only. The main use case is **a player reviewing their sortie**.

| ID | Page / feature | Priority | Status |
|---|---|---|---|
| FR-WEB-1 | **Mission list**: newest first, paginated. Shows name/map, date, duration, player count, and winner if known. | v1 | `[DECIDED]` |
| FR-WEB-2 | **Mission detail**: summary and the list of all sorties in the mission, grouped by coalition. | v1 | `[DECIDED]` |
| FR-WEB-3 | **Player search**: find a player by nickname (partial match, case-insensitive). HTMX live search. | v1 | `[PROPOSED]` |
| FR-WEB-4 | **Player profile**: identity (current nickname and maybe past nicknames), **totals and ratios**, and recent sorties. Totals: sorties, flight time, air kills, ground kills, assists, deaths, planes lost, bailouts (inferred), captures, landings. Ratios: **K/D** (kills per death), **K/L** (kills per plane lost), kills per sortie, kills per flight hour, survival rate. Also the same totals per aircraft type (small table). | v1 | `[DECIDED]` (2026-10-02: "totals, stuff like K/D, kills per plane lost, and so on"). Exact list `[PROPOSED]` |
| FR-WEB-5 | **Player sortie list**: all sorties by a player, paginated and filterable (by aircraft, maybe date). | v1 | `[PROPOSED]` |
| FR-WEB-6 | **Sortie detail**, the core page: aircraft, coalition, start type (air or ground), takeoff and landing times, flight time, outcome, kills and assists with victim details, damage dealt and taken (by whom), ammo used, and a chronological **event timeline**. | v1 | `[DECIDED]` (page). Exact contents `[PROPOSED]` |
| FR-WEB-7 | Leaderboards / rankings (by score, kills, and so on). | later | `[DEFERRED]`. Score concept to be designed later (2026-10-02) |
| FR-WEB-8 | Aircraft stats (performance per aircraft type). | later | `[DEFERRED]` |
| FR-WEB-9 | Killboard (player vs player). | later | `[DEFERRED]` |
| FR-WEB-10 | Tours or campaigns (grouping missions into periods). | later | `[OPEN]` OQ-4 |
| FR-WEB-11 | Awards / medals, squads, user registration. | later | `[DEFERRED]` |
| FR-WEB-12 | Sortie map (flight path, kill locations). Needs position data and map images. | later | `[DEFERRED]` |
| FR-WEB-13 | Stable, shareable URLs for missions, players, and sorties, so players can link a sortie on Discord. | v1 | `[PROPOSED]` |

## Server admin (FR-ADM)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ADM-1 | Django admin, available only to admin accounts created during setup. | v1 | `[PROPOSED]` |
| FR-ADM-2 | Set the site title, server name, and short description/links (for example a Discord link). | v1 | `[PROPOSED]` |
| FR-ADM-3 | Hide a player (privacy request or cheater) or a mission from public pages. | v1 | `[PROPOSED]` |
| FR-ADM-4 | See ingestion status: last processed mission, errors, unknown objects. | v1 | `[PROPOSED]` |
| FR-ADM-5 | Edit the object catalog (names and classes of game objects) without a new release. | v1 | `[PROPOSED]` |
| FR-ADM-6 | Customize branding (logo, colors) and templates without forking. | later | `[OPEN]` OQ-11 |
| FR-ADM-7 | Edit scoring values. | later | `[DEFERRED]` (with the score concept) |

## Operations (FR-OPS)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-OPS-1 | One command-line entry point (working name `il2ks`) with subcommands: `setup`, `ingest`, `watch`, `web`, `run` (web + watch together), `reprocess`, `createadmin`, `doctor` (checks the configuration). | v1 | `[PROPOSED]` |
| FR-OPS-2 | A single, commented configuration file holding the log path, DB connection, timezone, and web host/port. | v1 | `[PROPOSED]` |
| FR-OPS-3 | Database migrations run automatically on start or upgrade. | v1 | `[PROPOSED]` |
