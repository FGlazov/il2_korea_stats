# 02 — Functional Requirements

IDs are stable. Reference them in code comments, tests, and commits (for example `FR-ING-3`).
Priority: **v1** = first iteration (MVP), **it2** = second iteration, **later** = see [10_roadmap.md](10_roadmap.md).

## Ingestion (FR-ING)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ING-1 | Find mission log files in a configured log directory. That's either the DServer's own log folder, or a folder that logs get copied into from the game machine (FR-ING-16). | v1 | `[DECIDED]` |
| FR-ING-2 | Work out when a mission's logs are **complete** (the mission ended and no more files will be written) and only process complete missions. | v1 | `[PROPOSED]`: complete = AType 7 seen, **or** a newer mission's `[0]` file exists, **or** no new part for N minutes. AType 7 alone isn't reliable (4 of 210 samples have none). See [12](12_korea_log_format.md#timing) |
| FR-ING-3 | Parse every log line into a typed event. Unknown or malformed lines and unknown event types get logged, counted and kept, and **never crash ingestion**. The game may add event types every few years (TD-20). | v1 | `[DECIDED]` |
| FR-ING-4 | Replay a mission's events into a mission result: sorties, outcomes (landed, crashed, shot down, bailed out, and so on), kills and assists, damage dealt and taken, takeoffs and landings, ammo used, and positions of key events. | v1 | `[PROPOSED]` |
| FR-ING-5 | Save one mission atomically, all or nothing, in a single DB transaction. | v1 | `[PROPOSED]` |
| FR-ING-6 | **Idempotent**: processing the same mission twice must not create duplicates. Rerunning the job is always safe. | v1 | `[PROPOSED]` |
| FR-ING-7 | Game objects that aren't in the object catalog (new aircraft, vehicles) get stored as "unknown" and flagged for the admin. Processing continues. | v1 | `[PROPOSED]` |
| FR-ING-8 | Archive raw logs (compressed) after processing and **keep the archives forever** (by default). They're the source of truth for backfilling when stats logic changes (TD-08, TD-09). | v1 | `[DECIDED]` |
| FR-ING-9 | A `reprocess` command rebuilds the DB, or a set of missions, from archived logs after parser or logic fixes (backfill). | v1 | `[DECIDED]` |
| FR-ING-10 | Optionally delete original log files after a successful archive (off by default). | v1 | `[PROPOSED]` |
| FR-ING-11 | Record the result of each ingestion (mission, status, line counts, warnings, unknown event types and keys, duration) so the admin can see it. | v1 | `[PROPOSED]` |
| FR-ING-12 | **Online now**: current player counts and the list of players on the server, read from the in-progress mission's logs. | it2 | `[DECIDED]` |
| FR-ING-13 | **Import concatenated archives**: a single `.txt` or `.txt.zip` that holds a whole mission (the `il2_stats` backup format, and how `sample_data/` is stored). Used to load history and for dev/testing. | v1 | `[PROPOSED]` |
| FR-ING-14 | **Bailout detection without AType 18.** Detect pilots who left the aircraft in flight from the signals Korea *does* log (rule below), and store pilot fate with its source (`event` / `inferred` / `unknown`). Also flag **suspected early bailouts** (left an aircraft no attacker had touched). Switch to the real AType 18 if the game adds it back. | v1 | `[DECIDED]` (rule v2 for now; iterate later, 2026-10-02) |
| FR-ING-17 | **Mark self-destruction on the sortie.** Every lost aircraft gets a `loss_cause`: `attacker` (it was destroyed by a non-‑1 `AID`, or took any attacker hits or damage first) or `self` (destroyed by `AID:-1` with no attacker involvement: terrain impact, overstress, obstacle, or an abandoned aircraft). Plus a `suspected_structural_failure` flag (definition v2 below). Both show on the sortie page. | v1 | `[DECIDED]` (mark it), `[PROPOSED]` (definition v2, tested on sample data 2026-10-02) |
| FR-ING-15 | **Live sorties**: stream in-progress data so sorties appear right away, instead of after the mission ends. A stretch goal. | later (v2–v3+) | `[DEFERRED]` |
| FR-ING-16 | **Remote log mode**: the stats site runs on a different machine than DServer. Logs get copied into the configured folder (network share, sync tool, or a later `il2ks ship` helper). Ingestion must tolerate files that are still being copied (only read parts whose size is stable, or that have a newer sibling). | v1 (folder-based); helper later | `[DECIDED]` (support it), `[PROPOSED]` (mechanism) |

### Bailout rule v2 (FR-ING-14) — validated on 210 sample missions, 2026-10-02
Evidence and spot checks are in [12_korea_log_format.md](12_korea_log_format.md#pilot-bailout-detection-validated-2026-10-02).

**Bailout** (the pilot left the aircraft in flight). All of these must be true:
1. The pilot's sortie ends with **AType 4 `PLID:0`**, meaning the pilot wasn't in an aircraft. (Normal ends carry `PLID:<aircraft id>`.)
2. The aircraft was **airborne** at the moment it was destroyed (wheels-off state at its AType 3), or at sortie end if it wasn't destroyed.
3. The pilot's final position (AType 16) is **≥ 100 m from the aircraft's last known position**. This excludes crash landings and pilots who
   climb out next to the wreck.
4. The pilot didn't die together with the aircraft (pilot killed within 0.5 s of the aircraft's destruction means "died in the aircraft").

**Suspected early bailout:** a bailout where, before the aircraft was destroyed (or before the sortie ended),
5. the aircraft and pilot took **no hits and no damage from any attacker** (`AID` ≠ ‑1 and not the player themselves; the abandoned
   aircraft's own crash, `AID:-1`, doesn't count),
6. the player **didn't disconnect** (no AType 21 within 30 s of sortie end), and
7. the sortie didn't end **within 60 s of mission end**.

Results on the samples (11,550 sorties that took off): **1,456 bailouts (12.6%)**, of which 1,038 came after being attacked.
**308 suspected early bailouts (2.7%) across 174 accounts.** Spot checks of every category looked right after the distance check was added.

**How it evolved:** the maintainer's first rule (sortie ended, no disconnect, no landing in the last 60 s, no damage, not at mission end)
flagged 419 sorties. **416 of those were players who had landed and sat on the ground more than 60 s before despawning.** "No recent landing"
isn't the same as "in the air", and damage from the abandoned aircraft's own crash would hide real early bailouts. v2 keeps conditions 5–7
in spirit, and replaces "no recent landing" with the `PLID:0` + airborne + distance signals.

Pilot fate per sortie becomes: `in_aircraft` (landed, despawned, or died with the aircraft), `bailed_out` (inferred, rules 1–4),
`exited_on_ground` (`PLID:0`, not airborne or near the aircraft), `mission_ended` (the sortie was force-ended by mission end: AType 4
within a few seconds of AType 7; about 10% of sorties), or `unknown` (no AType 4, which in the samples is 99% disconnects).
The thresholds (100 m, 0.5 s, 30 s, 60 s) are config values.

**Status (maintainer, 2026-10-02):** keep rule v2 for now and iterate later. The maintainer will compare notes with the other developer.

**Known limitation: structural failure.** Overstressing an aircraft (for example tearing the wings off the F-86 by pulling too many G
at subsonic speed) is logged as self/environment damage (`AID:-1`). That's indistinguishable from the abandoned aircraft's own crash, so rule
condition 5 ignores it, and "broke my own aircraft, then bailed out" counts as a suspected early bailout. That's the likely reason the
F-86 has ~12 undamaged bailouts per 100 aircraft lost versus 1–4 for other types (evidence in
[12](12_korea_log_format.md#pilot-bailout-detection-validated-2026-10-02)). Both facts are recorded on the sortie (FR-ING-17), so the UI
can show "suspected early bailout" and "suspected structural failure" side by side. If penalties ever depend on it, a sortie with both flags
can be treated as structural failure instead of an early bailout.

### Self-destruction definition v2 (FR-ING-17) — tested on 210 sample missions, 2026-10-02
`suspected_structural_failure` is true when **all** of these hold:
1. `loss_cause = self`,
2. the aircraft was destroyed **airborne** (wheels-off state at its AType 3),
3. the self-damage began **< 1 s** before destruction (sudden),
4. the wreck **kept falling > 1 s** before its next ground contact (AType 31 or 6). This is the condition the first draft lacked.

Results (6,367 aircraft lost out of 11,550 sorties that took off): `loss_cause` is **attacker 3,172 / self 3,195**. Conditions 1–3 alone (draft v1) matched
2,675. That was far too broad: **628 were terrain impacts** (the wreck hit the ground within 1 s, and the pilot died with it in 94% of them). With condition 4:
**391 suspected structural failures** (wreck fell a median 7.7 s from a median 424 m). **F-86A-5: 17.6 per 100 aircraft lost; every other type
1.5–5.2.** That independently supports the maintainer's "the Sabre tears its own wings off" explanation. Limitations: 1,656 sudden airborne self-losses
log no ground contact afterwards, so they can't be classified and stay plain `self`. About 20% of the flagged ones were destroyed below 100 m
(probably obstacle or tree strikes). A minimum-altitude threshold is an easy later tweak. The thresholds are config values.

## Website: players (FR-WEB)

All pages are public and read-only. There are no player accounts or logins (decided: not in v1, maybe later if
people ask). The main use case is **a player reviewing their sortie**.

| ID | Page / feature | Priority | Status |
|---|---|---|---|
| FR-WEB-1 | **Mission list**: newest first, paginated. Shows name/map, date, duration, player count, and winner if known. | v1 | `[DECIDED]` |
| FR-WEB-2 | **Mission detail**: summary and the list of all sorties in the mission, grouped by coalition. | v1 | `[DECIDED]` |
| FR-WEB-3 | **Player search**: find a player by nickname (partial match, case-insensitive). HTMX live search. | v1 | `[PROPOSED]` |
| FR-WEB-4 | **Player profile**: identity (current nickname and past nicknames), all-time **totals and ratios** (per tour from it2), and recent sorties with a **link to the player's full sortie list** (FR-WEB-5). Totals: sorties, flight time, air kills, ground kills, assists, deaths, planes lost, bailouts, suspected early bailouts, captures, landings. Ratios: **K/D** (kills per death), **K/L** (kills per plane lost), kills per sortie, kills per flight hour, survival rate. Also the same totals per aircraft type (small table). Each aircraft row may link to the sortie list filtered to that aircraft. | v1 | `[DECIDED]` (totals and ratios; sortie-list link, 2026-10-02). Exact list and per-aircraft links `[PROPOSED]` |
| FR-WEB-5 | **Player sortie list**: all sorties by a player, paginated and filterable by aircraft (and by tour from it2). Reached from the player profile (FR-WEB-4). | v1 | `[DECIDED]` (page and profile link, 2026-10-02), `[PROPOSED]` (filters) |
| FR-WEB-6 | **Sortie detail**, the core page: aircraft, coalition, start type (air or ground), takeoff and landing times, flight time, outcome, kills and assists with victim details, damage dealt and taken (by whom), ammo used, and a chronological **event timeline**. | v1 | `[DECIDED]` (page). Exact contents `[PROPOSED]` |
| FR-WEB-7 | Leaderboards / rankings (by score, kills, and so on). | later | `[DEFERRED]`. Score concept to be designed later |
| FR-WEB-8 | Aircraft stats (performance per aircraft type). | later | `[DEFERRED]` |
| FR-WEB-9 | Killboard (player vs player). | later | `[DEFERRED]` |
| FR-WEB-10 | **Tours**: missions grouped into periods. Stats are shown per tour and all-time. The tour length is **configurable**, by calendar month (default), N days, or started manually by the admin. | it2 | `[DECIDED]` (tours in it2, configurable length), `[PROPOSED]` (exact modes) |
| FR-WEB-11 | Awards / medals, squads, player accounts and registration. | later | `[DEFERRED]` |
| FR-WEB-12 | Sortie map (key event locations: takeoff, kills, bailout, landing). v1 stores those positions; the map page comes later. Not a continuous flight path, since the logs have no periodic position updates. | later | `[DEFERRED]` (page), positions stored from v1 (TD-08) |
| FR-WEB-13 | Stable, shareable URLs for missions, players, and sorties, so players can link a sortie on Discord. | v1 | `[PROPOSED]` |
| FR-WEB-14 | **Gunner stats**: player gunners (for example IL-10 turret) as a separate stats view. | later | `[DEFERRED]` (nice to have). v1 still records gunner sorties, just doesn't show dedicated pages |
| FR-WEB-15 | **In-progress missions and current player counts** on the main page. | it2 | `[DECIDED]` |
| FR-WEB-17 | **Times shown in the viewer's local timezone**: a player in Japan and one in Europe each see mission and sortie times in their own local time, without configuring anything. Game-world time (the in-mission date and time) and durations aren't converted. Until then, pages show UTC and label it as such. | later (stretch) | `[DEFERRED]` (nice to have, not in the PoC, 2026-10-02), mechanism `[PROPOSED]` (TD-15) |
| FR-WEB-16 | **Light charts** where they help (for example kills per tour, sorties over time). The site is mostly tables. | later | `[PROPOSED]` |

## Server admin (FR-ADM)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ADM-1 | Django admin, available only to admin accounts created during setup. | v1 | `[PROPOSED]` |
| FR-ADM-2 | **Branding without touching files**: site title, server name, logo, accent colors, short description and links (for example Discord), all set in the admin UI. | v1 | `[DECIDED]` |
| FR-ADM-3 | Hide a player (privacy request or cheater) or a mission from public pages. | v1 | `[PROPOSED]` |
| FR-ADM-4 | See ingestion status: last processed mission, errors, unknown objects, unknown event types. | v1 | `[PROPOSED]` |
| FR-ADM-5 | Edit the object catalog (names and classes of game objects) and the display names of coalitions and countries without a new release. Game object names ship with **project-set defaults, including translations**. Admin edits are optional overrides that survive upgrades and can be reset (TD-24). | v1 | `[DECIDED]` (object-name defaults and overrides, 2026-10-02), `[PROPOSED]` (rest) |
| FR-ADM-6 | **Template and static overrides**: a `custom/` folder (in the data directory, so it survives upgrades) whose templates and static files take priority over the built-in ones. This is very important for some server owners. | v1 | `[DECIDED]` (TD-25) |
| FR-ADM-7 | Edit scoring values. | later | `[DEFERRED]` (with the score concept) |
| FR-ADM-8 | Start a new tour manually (when the tour mode is manual), and rename tours. | it2 | `[PROPOSED]` |

## Operations (FR-OPS)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-OPS-1 | One command-line entry point, **`il2ks`**, with subcommands: `setup`, `ingest`, `watch`, `web`, `run` (web + watch + HTTPS proxy together), `reprocess`, `createadmin`, `doctor` (checks the configuration), `db copy`. | v1 | `[DECIDED]` (name `il2ks`), `[PROPOSED]` (subcommands) |
| FR-OPS-2 | A single, commented configuration file holding the log path, DB connection, timezone, domain/HTTPS settings, bailout rule thresholds, and (it2) tour mode. | v1 | `[PROPOSED]` |
| FR-OPS-3 | Database migrations run automatically on start or upgrade. | v1 | `[PROPOSED]` |
| FR-OPS-4 | **HTTPS only.** The site is served over HTTPS, and plain HTTP only redirects (TD-23). | v1 | `[DECIDED]` |
| FR-OPS-5 | **One install per game server.** Several installs on the same machine (the rare multi-DServer case) must coexist: separate data directories, ports, and service names, with nothing hard-coded. | v1 | `[DECIDED]` |
