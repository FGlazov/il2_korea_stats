# 02 — Functional Requirements

IDs are stable. Reference them in code comments, tests, and commits (for example `FR-ING-3`).
Priority: **v1** = first iteration (MVP), **it2** = second iteration, **later** = see [10_roadmap.md](10_roadmap.md).

## Ingestion (FR-ING)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ING-1 | Find mission log files in a configured log directory. That's either the DServer's own log folder, or a folder that logs get copied into from the game machine (FR-ING-16). | v1 | `[DECIDED]` |
| FR-ING-2 | Work out when a mission's logs are **complete** (the mission ended and no more files will be written) and only process complete missions. | v1 | `[PROPOSED]`: complete = a newer mission's `[0]` file exists, **or** no new part for 10 minutes, **or** AType 7 seen and no new part for 5 minutes. AType 7 alone isn't reliable (4 of 210 samples have none). The reason is stored per run. See [12](12_korea_log_format.md#timing), [14](14_ingest_internals.md#ingest-jobs) |
| FR-ING-3 | Parse every log line into a typed event. Unknown or malformed lines and unknown event types get logged, counted and kept, and **never crash ingestion**. The game may add event types every few years (TD-20). | v1 | `[DECIDED]` |
| FR-ING-4 | Replay a mission's events into a mission result: sorties, outcomes (landed, crashed, shot down, bailed out, and so on), kills and assists, damage dealt and taken, takeoffs and landings, ammo used, and positions of key events. | v1 | `[PROPOSED]` |
| FR-ING-5 | Save one mission atomically, all or nothing, in a single DB transaction. | v1 | `[PROPOSED]` |
| FR-ING-6 | **Idempotent**: processing the same mission twice must not create duplicates. Rerunning the job is always safe. | v1 | `[PROPOSED]` |
| FR-ING-7 | Game objects that aren't in the object catalog (new aircraft, vehicles) get stored as "unknown" and flagged for the admin. Processing continues. | v1 | `[PROPOSED]` |
| FR-ING-8 | Archive raw logs (compressed) **before** the DB commit, verify the archive, and **keep the archives forever** (by default). They're the source of truth for backfilling when stats logic changes (TD-08, TD-09). Each mission records its archive path and checksum. On startup a **reconcile** step archives any ingested mission that lacks a verified archive while its source files still exist (see the pipeline in 04). | v1 | `[DECIDED]` (archive forever), `[PROPOSED]` (archive-first ordering and reconcile, 2026-10-02 review) |
| FR-ING-9 | A `reprocess` command rebuilds the DB, or a set of missions, from archived logs after parser or logic fixes (backfill). It **updates rows in place by natural key** (upsert), so mission, sortie and player URLs survive (FR-WEB-13). Rows that no longer exist after the rerun are deleted. Missions can be selected by UID or by **date range** (`--since` / `--until`, for example "scoring changed, redo the last 6 months"). | v1 | `[DECIDED]` (reprocess, date range 2026-10-03), `[PROPOSED]` (upsert by natural key) |
| FR-ING-10 | **Move original part files out of the game's log folder by default** once their archive is verified. A mission leaves ~690 part files, so keeping them would mean ~2 million files a year and slow discovery. Configurable: `logs.after_archive = "move"` (default) / `"keep"` / `"delete"`. Use `keep` if another tool also reads the folder. | v1 | `[PROPOSED]` (2026-10-02 review; was "off by default") |
| FR-ING-18 | **Late parts re-ingest**: each ingested mission stores the fingerprint of its part files (names, sizes, mtimes). If discovery later finds new or changed parts for an ingested mission (for example after a copy tool stalled in remote mode), that mission is **re-ingested** (in place, FR-ING-9). Missions completed only by the idle timeout are marked `completed_cleanly = false`, and the admin sees them. | v1 | `[PROPOSED]` (2026-10-02 review) |
| FR-ING-19 | **Retry policy for failed missions**: retry with backoff (for example after 5 min, 30 min, 2 h, then stop) and show the failure in admin. A retry also happens when the mission's file fingerprint changes or a new `il2ks` version is installed. Admins can force one with `il2ks reprocess --mission <key>`. Never retry every watch tick. | v1 | `[PROPOSED]` (2026-10-02 review) |
| FR-ING-20 | **Single writer**: `watch`, a scheduled `ingest`, and `reprocess` take one exclusive lock (a lock file in the data directory, holding the PID, with stale-lock detection) before writing. A second writer waits or exits with a clear message. `reprocess` parallelizes only parse and replay (worker processes). One writer process does all DB writes, since SQLite allows one writer. | v1 | `[PROPOSED]` (2026-10-02 review) |
| FR-ING-21 | **Disconnecting while still in the aircraft counts as a death (and an aircraft lost) only if the aircraft or pilot took damage in the last 2 minutes** before the disconnect, **from any source, including self-damage** (overstress, crash damage, collisions). A disconnect without recent damage is neither a death nor a loss (maintainer, 2026-10-02; replaces "every airborne disconnect is a death"). Applies to a player who disconnects (AType 21, or a sortie with no AType 4, which is 99% disconnects) without having left the aircraft. Credit: if an attacker caused any of that recent damage, the attacker gets the kill by the abandoned-aircraft rules (FR-ING-22). If it was only self-damage, the death counts but `loss_cause = self`. The 2-minute window is a config value. | v1 | `[DECIDED]` (2026-10-02) |
| FR-ING-22 | **Kill credit for abandoned aircraft**: when a player bails out (FR-ING-14) or disconnects after being damaged by an attacker, and the aircraft is then destroyed by the environment (`AID:-1`: crash, or the abandoned aircraft's own destruction), **the attacker gets the kill**, chosen by the ported `il2_stats` damage-based credit (most damage, with assists for others), TD-21. Without attacker damage the loss stays `loss_cause = self` (FR-ING-17). | v1 | `[DECIDED]` (2026-10-02) |
| FR-ING-23 | **Friendly fire is tracked separately**: per sortie (and summed per player, mission and aircraft) friendly kills, friendly hits and friendly damage. Friendly = same non-zero coalition, not the sortie's own objects. Friendly kills never count as kills or assists, and show in the timeline as `friendly_fire`. | v1 | `[DECIDED]` (maintainer, 2026-10-03) |
| FR-ING-24 | **Resupply and ammo used**: players can land, rearm and take off again in one sortie, so "loaded − left" (AType 10 / AType 4) undercounts ammo used. Gun bursts (AType 24) carry no round count. Whether resupply is allowed is a server setting, so il2ks has a config switch `replay.resupply_allowed` (default `true`). Detection and reporting rule: see [13](13_game_rules.md#ammo-and-resupply). | v1 | `[DECIDED]` (config switch), `[PROPOSED]` (rule) |
| FR-ING-11 | Record the result of each ingestion (mission, status, line counts, warnings, unknown event types and keys, duration) so the admin can see it. | v1 | `[PROPOSED]` |
| FR-ING-12 | **Online now**: current player counts and the list of players on the server, read from the in-progress mission's logs. | it2 | `[DECIDED]` |
| FR-ING-13 | **Import concatenated archives**: a single `.txt` or `.txt.zip` that holds a whole mission (the `il2_stats` backup format, and how `sample_data/` is stored). Used to load history and for dev/testing. | v1 | `[PROPOSED]` |
| FR-ING-14 | **Bailout detection without AType 18.** Detect pilots who left the aircraft in flight from the signals Korea *does* log (rule below), and store pilot fate with its source (`event` / `inferred` / `unknown`). Also flag **suspected early bailouts** (left an aircraft no attacker had touched). Switch to the real AType 18 if the game adds it back. | v1 | `[DECIDED]` (rule v2 for now; iterate later, 2026-10-02) |
| FR-ING-17 | **Mark self-destruction on the sortie.** Every lost aircraft gets a `loss_cause`: `attacker` (it was destroyed by a non-‑1 `AID`, or took any attacker hits or damage first) or `self` (destroyed by `AID:-1` with no attacker involvement: terrain impact, overstress, obstacle, or an abandoned aircraft). Plus a `suspected_structural_failure` flag (definition v2 below). Both show on the sortie page. | v1 | `[DECIDED]` (mark it), `[PROPOSED]` (definition v2, tested on sample data 2026-10-02) |
| FR-ING-15 | **Live sorties**: stream in-progress data so sorties appear right away, instead of after the mission ends. A stretch goal. **Approach** (maintainer, 2026-10-03): resolve the running mission every few minutes (e.g. 5) with the same rules as the final pass (`snapshot()`, TD-07). Each pass overwrites the previous provisional result. Sortie and mission pages of a running mission show a notice that the mission is still in progress and the result may change when it ends. **Before the release** (maintainer, 2026-10-04): one live pipeline feeds the online-now list and the provisional sorties; an admin toggle, on by default; notices on the sortie and mission pages. | release | `[DECIDED]` (feature and approach, maintainer 2026-10-04), `[PROPOSED]` (pass interval, provisional rows in boards) |
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
`exited_on_ground` (`PLID:0`, not airborne or near the aircraft), (a sortie force-ended by the mission end, AType 4 within a few seconds of AType 7, about 10% of sorties, keeps
`in_aircraft` and is flagged `ended_by_mission_end`, 2026-10-03), `disconnected` (no AType 4 or an AType 21 near the end; 99% of no-AType-4 cases. Counts as a **death** only with damage in the last 2 minutes, FR-ING-21), or `unknown`.
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

### Ammo attribution rule (FR-WEB-18)
Damage lines (AType 2) carry no ammo type. Hit lines (AType 1) do. So each damage line is attributed to the ammo of the **hit closest in time**
for the same attacker → target pair (the maintainer's improvement: the old module used the *next* hit, which was "hacky"). A tie goes to the
hit on the same tick. Damage with no hit within a configurable window (a few ticks) stays "unattributed" instead of being guessed. **"explosion" is never shown**: the UI always names the
ordnance ("hit: M65 1000 lb bomb", "hit: HVAR 5\" rocket"), never "hit: explosion" (maintainer, 2026-10-02).

Why replay still reads `AMMO:explosion` lines (in memory only, never stored, TD-08): for bombs and rockets the logs almost never write a
hit line with the ordnance name. In the samples there were 572 `BOMB_*` and 1,186 `RKT_*` hit lines against 1.9 M `explosion` hit lines
(30 missions), and for 97% of player-caused damage lines the closest hit is an explosion. If explosion lines were ignored, almost all
bomb and rocket damage would be unattributed, or wrongly matched to a gun hit seconds away. So an explosion hit is used as the **link**
between a damage line and the ordnance, and the ordnance name is what gets counted and shown. Where a direct `BOMB_*`/`RKT_*` hit line
exists, it's used as is.

**Checked 2026-10-03 (30 missions, 287k detonations = explosion hits grouped by attacker and tick):**
- **95.4% of detonations have no bomb/rocket/napalm/shell hit line within 1 s**, so the named ordnance lines can't replace explosion lines.
  The named lines seem to cover only direct impacts on an object. Blast and fire damage only appear as "explosion".
- The explosions are still reliably ordnance: of the detonations without a nearby named line, **99.4% come from aircraft that released
  stores or rockets** (59% within the previous 60 s, 40% earlier in the sortie), and the payload file confirms **98.6% carried bombs,
  rockets or napalm** (0.7% empty or drop tanks only). 94% of those loadouts include **napalm**, which probably explains the long-delayed
  explosions (burning napalm). 1.8% of detonations sit next to cannon shell hits (23/37 mm HE/API, MiG and La), so a few are cannon fire.

**Explosion filter measured, not adopted** (2026-10-04): 99.96% of explosion hits come from player aircraft, so dropping the AI ones early saves nothing; coalescing same-tick bursts is in progress (doc 14 "Speed").

**Explosion lines are never counted as hits** `[DECIDED]` (maintainer, 2026-10-03). Per target, checked 2026-10-03 on 30 missions
(1.12 M detonation × target pairs): **99.2% of targets touched by a detonation get only explosion lines**, with no named ordnance hit. A
detonation touches a median of 3 targets (p90 8, max 66) with 1–9 explosion lines each, and only ~16% of those pairs take damage. So:
- **Ordnance counting unit = one detonation × one target that took damage** ("targets damaged"). Raw explosion lines and harmless splash
  count for nothing.
- Per ordnance type, the breakdown shows: **released** (AType 25/26), **detonations**, **targets damaged**, **kills**. Guns keep counting
  bullet and shell hit lines as hits.
- Hits-to-destroy per aircraft type (above) counts **gun hits only** (bullet and shell hit lines) `[DECIDED]` (maintainer, 2026-10-03):
  bombs and rockets almost never hit aircraft in Korea. Revisit if air-to-air missiles are added to the game.

**Labelling rule for an explosion hit** `[PROPOSED]`, in order:
1. A named ordnance or shell hit line from the same attacker within 1 s: use that ammo.
2. The aircraft's loadout (payload file) has exactly one ordnance type: use it.
3. A store or rocket released within the previous 60 s: use that release's ordnance type (AType 25/26 plus the store object's type).
4. Napalm was released earlier in the sortie: napalm (lingering fire).
5. Otherwise: "unattributed" (never shown as "explosion").

**Checked on 30 sample missions (2026-10-02):** sorties that released stores or rockets produce ~1,769 explosion hits each, versus 18–36 for
gun-only sorties and ~0 for sorties without hits. So explosions are overwhelmingly ordnance. For 97% of player-caused damage lines the
closest hit is an explosion, but for **about a third the closest hit of any kind is more than 1 s away**. Those stay unattributed under the
window rule (they could be fire or secondary damage; investigate during implementation).

## Website: players (FR-WEB)

All pages are public and read-only. There are no player accounts or logins (decided: not in v1, maybe later if
people ask). The main use case is **a player reviewing their sortie**.

| ID | Page / feature | Priority | Status |
|---|---|---|---|
| FR-WEB-1 | **Mission list**: newest first, paginated. Shows name/map, date, duration, player count, and winner if known. | v1 | `[DECIDED]` |
| FR-WEB-2 | **Mission detail**: summary and the list of all sorties in the mission, grouped by coalition. | v1 | `[DECIDED]` |
| FR-WEB-3 | **Player search**: find a player by nickname (partial match, case-insensitive). HTMX live search. | v1 | `[PROPOSED]` |
| FR-WEB-4 | **Player profile**: identity (current nickname and past nicknames), all-time **totals and ratios** (per tour from it2), and recent sorties with a **link to the player's full sortie list** (FR-WEB-5). Totals: sorties, flight time, air kills, ground kills, assists, deaths, planes lost, bailouts, suspected early bailouts, captures, landings, plus two "hall of shame" totals: **taxi accidents** (aircraft lost on the ground before takeoff, by the player's own doing) and **times strafed on the ground** (aircraft destroyed on the ground by an attacker), maintainer 2026-10-03 (doc 13). **Ground kills are broken down** by category (tanks, vehicles such as trucks, artillery, AA, ships, trains, buildings, parked aircraft, other statics) and by static vs. moving, shown in a collapsible element next to the total, so a total inflated by fences and boxes stays honest (maintainer, 2026-10-03, OQ-33; the same breakdown on the sortie page). Ratios: **K/D** (kills per death), **K/L** (kills per plane lost), kills per sortie, kills per flight hour, survival rate. Also the same totals per aircraft type (small table). Each aircraft row may link to the sortie list filtered to that aircraft and has a **favourite loadout** line (share of sorties, with all loadouts, weapon-modification sets and the gun ammo mix in a `<details>`; `PlayerAircraftBuild`, maintainer request 2026-10-04). **As built** (2026-10-04, maintainer: air and ground apart, shame and recent sorties near the top): header, tour selector, an in-page nav, general tiles, hall of shame, an achievements medal row (FR-WEB-26), the **latest 5 sorties** with a "View all sorties" button (FR-WEB-5), then an **air-to-air part** (kills, ratios with stat marks, air score, interception, Elo, air kills by victim, the killboards by aircraft type and by pilot), an **air-to-ground part** (ground kills with the breakdown, ground score, score per hour and tanks per hour on target) and an **overall part** (ironman streaks, per-aircraft table, PvE, other totals, charts); a part with no activity in the scope collapses to one line. Details: doc 16 "Pages". | v1 | `[DECIDED]` (totals and ratios; sortie-list link, 2026-10-02). Exact list and per-aircraft links `[PROPOSED]` |
| FR-WEB-5 | **Player sortie list**: all sorties by a player, paginated and filterable by aircraft (and by tour from it2). Reached from the player profile (FR-WEB-4). **As built** (2026-10-04): the default columns are start time, aircraft, role, outcome with the **pilot fate** next to it (Dead / Captured / Survived, OQ-106), damage taken, air kills, ground kills, assists and flight time; Mission and more are optional columns (FR-WEB-27, OQ-109). | v1 | `[DECIDED]` (page and profile link, 2026-10-02), `[PROPOSED]` (filters) |
| FR-WEB-6 | **Sortie detail**, the core page: aircraft, coalition, start type (air or ground), takeoff and landing times, flight time, outcome, kills and assists with victim details, damage dealt and taken (by whom), ammo used, and a chronological **event timeline**. **As built** (2026-10-04): the pilot fate is shown as Dead / Captured / Survived with the stored fate (bailed out, exited on ground, left the server) as detail (OQ-106; stored values unchanged, doc 13); the timeline lists **significant hits given and taken** with a signed damage % column and the ammo of the nearest hit (doc 13 "Timeline hits", OQ-108); the ammo table shows what is known after a loss (doc 13 "Ammo and resupply", OQ-101); medals earned in the sortie (FR-WEB-26) and a quip for notable sorties (FR-WEB-23). | v1 | `[DECIDED]` (page). Exact contents `[PROPOSED]` |
| FR-WEB-7 | Leaderboards / rankings. Score is **split into an air score and a ground score** (air-to-air and ground-attack skill are rated differently). See [Score and ratings](#score-and-ratings). **As built** (2026-10-04, order decided by the maintainer, OQ-84): seven boards in two groups, **Air**: Elo jet, Elo prop, air score, interception; **Ground**: ground score per hour (on target), tank busting, ground score. **No kills board**: `/leaderboards/kills/` answers 301 to `/leaderboards/`. A row of **icon buttons** (a `div role=navigation` with a label above the buttons) switches boards and keeps the tour, propulsion and aircraft choice where the target board has the filter. Every board except the Elo boards (all time only) has the tour selector (current tour by default, `?tour=all` for all time, TD-26), an aircraft-type filter and a prop/jet filter; minimums keep one lucky sortie off a board (`[score]`, doc 13). Links from an all-time view carry `?tour=all` so they stay all time (`queries.tours.tour_query`, TD-26). The home page shows the top 5 of six boards in a 3x2 grid (Elo jet, Elo prop, interception, ground score per hour, tank busting, play time; maintainer, OQ-104; doc 16). | it1.x (built) | `[DECIDED]` (air/ground split, 2026-10-03; Elo and ground proficiency on the home page, OQ-64; board order and no kills board, OQ-84), `[DECIDED]` (home boards, OQ-104; "encounters" for Elo games in the UI), `[PROPOSED]` (layout) |
| FR-WEB-19 | **Air-to-air Elo** for fighter-vs-fighter combat, with separate **prop and jet** ratings, and **interception**: bombers, attackers and transports shot down per hour of air superiority flight (an air superiority pilot's skill against the big targets; doc 13 "Interception and tank busting", OQ-102). See [Score and ratings](#score-and-ratings). | it1.x (built); ratings computed at ingest from it1 | `[DECIDED]` (Elo rules, 2026-10-03; details `[PROPOSED]`; interception board and its definition, maintainer 2026-10-04, OQ-102); pages as built `[PROPOSED]` |
| FR-WEB-20 | **Ground-attack proficiency**: ground score per hour **on target** for attack sorties (transit to and from the target excluded), and **tank busting**: tanks destroyed in attack sorties per hour on target (doc 13 "Interception and tank busting", OQ-103). See [Score and ratings](#score-and-ratings). | it1.x (built); time on target stored per sortie from it1 | `[DECIDED]` (time-on-target rule, 2026-10-03; tank busting board and its definition, maintainer 2026-10-04, OQ-103); boards `[PROPOSED]` |
| FR-WEB-21 | **PvE breakdown**: who killed me and whom I killed, by counterpart class (player, AI aircraft, AI gunner, AAA, ground vehicle, environment), so a player can answer "how often do AA or AI gunners get me?". AI-vs-AI is not tracked. The data is already in the replay output (`KillResult` covers every kill with a player on either side); the profile and sortie pages need the per-class counters. | it1.x | `[DECIDED]` (PvE kills and deaths matter, maintainer 2026-10-03), `[PROPOSED]` (iteration) |
| FR-WEB-8 | **Aircraft stats** (performance per aircraft type). **As built** (2026-10-04): an Aircraft list (all flown types, hidden players included in totals) and a detail page per type with matchups against each enemy type, top pilots ranked by per-type Elo and, for attack work, ground score per hour on target (an **attack type**, one with more attack than air-superiority sorties, lists the ground ranking first; maintainer, OQ-86), hits to destroy per ammo, and loadouts. **Matchups per tour** (2026-10-04): the matchup table follows the tour selector (`AircraftMatchup.tour`) and has a filter **"Intercept flights only"** (both sorties air superiority, `AircraftMatchup.intercept`; not the interception board), is sortable, shows the exchange share only from 10 fights and names the best and worst exchange (maintainer, OQ-110: 10 fights minimum, as built). The aircraft list needs no stored ratios: K/D, K/L, survival and attack share are computed and sorted in SQL (OQ-98); optional columns: FR-WEB-27. **Per tour** (maintainer, 2026-10-04): the list and the detail tiles and matchups follow one tour selector (`TourAircraftStats`); top pilots, hits to destroy, loadouts and the side badge stay all time (OQ-114). **Before the release** (maintainer, 2026-10-04): an optional "significant modifications" filter (MiG-15bis Anti-G suit, NR-23 cannons, improved air brakes and wing, in all combinations; F-51D 150-grade fuel) with a mods table, from `weapon_mods.csv` (WM bit k = mod k): **not built yet**; the foundation is (mod names on the sortie page, the `weapon_mods` bitmask and the profile's loadout sets). | it1.x (built) | `[DECIDED]` (page, ranking by skill, OQ-49/50/65; matchups per tour with an intercept filter and no stored ratios, maintainer 2026-10-04, OQ-98), `[PROPOSED]` (details) |
| FR-WEB-9 | **Killboard** (player vs player): per pair, how often each shot the other down, and the last encounter; on the profile (top 5 each way) and as a full page, all time or per tour. **As built** (2026-10-04): a `[killboard] assists` config toggle (default off) adds a column of assist credits and the opponent's "last encounter" counts assists too; **assists received** (the opponent's assists on the player's sorties) are a detail, not a column (maintainer, OQ-81; `assists_received` on `PlayerPair`); hidden opponents sort last; tie-breaks as built (OQ-83). **Killboard by aircraft type** (2026-10-04, maintainer): above the player-vs-player table, and in short on the profile, the **enemy aircraft types the pilot shot down most** and the types that **killed them most**, each with the pilot's own type most used in those fights, all time and per tour (`PlayerTypeKillboard`; PvP air kills only, no assists; hidden opponents count). | it1.x (built) | `[DECIDED]` (feature, assists toggle off by default), `[PROPOSED]` (details) |
| FR-WEB-25 | **Ironman streaks**: runs of consecutive sorties a pilot survived (rule in `core/streaks.py`, OQ-57/58): a running-streaks list, and per player a sub-page with the **best streaks** by sorties survived, air kills and flight time, all time and per tour (a tour streak counts only that tour's sorties), plus a **history page** `/players/<id>/streaks/history/` linked from the player's sortie page with every run of at least 2 sorties, newest first, paginated, all time or per tour (maintainer, OQ-82). Tie-breaks between equal bests: by air kills, more sorties, more flight time, the earlier run; by flight time, more sorties, more air kills, the earlier (OQ-83). Built 2026-10-03; the code and the build notes call this FR-WEB-23, which is the flavor-text requirement (stale reference). | it1.x (built) | `[DECIDED]` (streaks and own-best-streaks page, 2026-10-03), `[DECIDED]` (history page, OQ-82; tie-breaks, OQ-83), `[PROPOSED]` (details) |
| FR-WEB-26 | **Achievements / medals** (the medals part of FR-WEB-11): tiered medals computed at ingest from a pilot's sorties, a medal row on the profile, "earned in this sortie" on the sortie page, a full list per player and an overview with holder counts. Per tour (all time next to it), with the share of pilots holding each tier as hover text, rarer tiers standing out, ribbons for the simpler achievements and a "Recently earned" strip on the home page (OQ-105, doc 17). Design, rules, thresholds and ideas: [17](17_achievements.md). | it1.x (built) | `[PROPOSED]` (maintainer request 2026-10-04; names, thresholds and display for review) |
| FR-WEB-27 | **Optional columns** on the player search, mission list, aircraft list and a player's sortie list (maintainer, 2026-10-04): the default view stays as it is; a "Columns" control adds sortable columns (Elo, K/D, K/L, survival, scores, ground per hour, ... per list), kept in the URL (`?cols=a,b`) so links are shareable. Ratios are sorted in SQL with NULL (no denominator) always last. Code: `web/columns.py`, `queries/sorting.py` (`Ratio`, `Rated`); doc 16. | it1.x (built) | `[DECIDED]` (feature), `[DECIDED]` (Mission optional on the sortie list, OQ-109), `[PROPOSED]` (column choices) |
| FR-WEB-28 | **Language selection**: a footer menu with the language's own name and a country flag (US flag for English, "Português" for Brazilian Portuguese), working without JavaScript. With no explicit choice the site uses the browser's language (`LocaleMiddleware`; a regional variant such as `pt-PT` maps to the shipped language, else English); the choice is remembered in a cookie and wins over the browser (TD-24, doc 16). | it1.x (built) | `[DECIDED]` (maintainer, 2026-10-04) |
| FR-WEB-10 | **Tours**: missions grouped into periods. Stats are shown per tour and all-time. The tour length is **configurable**, by calendar month (default), N days, or started manually by the admin. **Pages open on the current tour** (profile, mission list, player sortie list, killboard, leaderboards) with a tour dropdown (top entries "All time" and "Current tour", no segmented toggle: OQ-78) and a "next tour starts" line (OQ-45..48, OQ-78..80). | it2 (built) | `[DECIDED]` (tours in it2, configurable length), `[PROPOSED]` (exact modes) |
| FR-WEB-11 | Awards / medals, squads, player accounts and registration. | later | `[DEFERRED]` |
| FR-WEB-12 | Sortie map (key event locations: takeoff, kills, bailout, landing). v1 stores those positions; the map page comes later. Not a continuous flight path from the logs, since they have no periodic position updates. A full flight path becomes possible if a separate telemetry source is added (TD-08, OQ-26). **Benched until after the release** (maintainer, 2026-10-03, OQ-54/55): the built grid version is not on main. | later | `[DEFERRED]` (page), positions stored from v1 (TD-08) |
| FR-WEB-13 | Stable, shareable URLs for missions, players, and sorties, so players can link a sortie on Discord. They **survive `reprocess`** because rows are updated in place by natural key: mission = `(server_uid, mission_uid)`, sortie = mission + player account UUID + spawn tick, player = account UUID (FR-ING-9). | v1 | `[PROPOSED]` |
| FR-WEB-14 | **Gunner stats**: player gunners (for example IL-10 turret) as a separate stats view. **Credit rule** (maintainer, 2026-10-03, OQ-31): with an **AI gunner** the pilot gets the gunner's kills; with a **player gunner** the gunner gets the kill and the pilot an assist. The log credits all of an aircraft's fire to the aircraft (doc 12), so this needs telling the gunner's fire apart first; the ammo type is the likely handle (the IL-10 turret fires 12.7 mm, the forward guns 23 mm and 7.62 mm). | it2 stretch (maintainer, 2026-10-03) | `[DECIDED]` (stretch goal in it2, with the credit rule). v1 still records gunner sorties, just doesn't show dedicated pages |
| FR-WEB-15 | **In-progress missions and current player counts** on the main page. | it2 | `[DECIDED]` |
| FR-WEB-18 | **Ammo breakdown** (port of the maintainer's `il2_stats` module, which players liked). Per sortie: hits **given and received per ammo type** (bullets and shells, with bombs and rockets listed separately), plus the damage attributed to each ammo type. Per aircraft type: the **average number of hits of each ammo type needed to destroy it**, counted only from kills where all damage came from one attacker (as in the old module), with the number of counted kills (**Instances**) and the **ammunition mixes** (which ammo types hit together, OQ-116). Attribution rule below. | **it1.x**, **not a release gate** | `[DECIDED]` (feature and iteration, 2026-10-02; the public release doesn't wait for it), `[PROPOSED]` (exact pages) |
| FR-WEB-17 | **Times shown in the viewer's local timezone**: a player in Japan and one in Europe each see mission and sortie times in their own local time, without configuring anything. Game-world time (the in-mission date and time) and durations aren't converted. **As built** (2026-10-04): `localtime.js` formats times with `Intl.DateTimeFormat` (`dateStyle: medium`, `timeStyle: short`) in the page language and the browser's zone; the zone is named only in the footer, UTC stays in the tooltip; without JS pages show UTC and say so. | built | `[DECIDED]` (before release, 2026-10-03), mechanism `[PROPOSED]` (TD-15) |
| FR-WEB-22 | **Stat highlights**: a player's numbers are put in context. A ratio (survival rate, K/D, kills per sortie, ...) that is unusually good compared with every player who has enough sorties gets a tasteful highlight (for example above the 90th percentile); unusually bad gets at most a gentle, non-shaming hint, so nobody is demotivated. The thresholds are computed at ingest (level 2), views only read them. **As built** (2026-10-04, maintainer): marks also for **Elo jet and Elo prop, air score, ground score, ground score per hour, interception and tank busting**; each population follows the board it sits next to, so a mark means "better than N in 10 pilots on that board" (the pilots meeting the board's minimum; `StatThreshold.min_sorties` holds the minimum in the metric's own unit: sorties, rated games or seconds; Elo marks are all time only). | stretch, before the public release | `[DECIDED]` (feature and tone, maintainer 2026-10-03), method `[PROPOSED]` |
| FR-WEB-23 | **Flavor text**: light-hearted one-liners as a highlight feature in a few places (like the hall-of-shame quips), each with several variants. Tasteful, not on every section. **Sortie quips** (as built 2026-10-04, maintainer: more for extreme events): one spot per sortie, the first match wins in this order: taxi accident, friendly fire, captured, shot down by an AI gunner, ditched, shot down by AA, **bomber hunter** (2+ kills of bombers, attackers or transports), **ace** (3+ air kills), **stolen kills** (**air** assists: 2+ with no air kill, 3+ with one, 6+ with two, four reworded variants: maintainer, OQ-107), **battered victor** (landed with 50%+ damage and 2+ kills), limped home (landed with 50%+ damage), **ground pounder** (70+ ground kills), **quick first kill** (within 7 minutes of takeoff), **marathon** (1 hour or more). Thresholds are read off the September 2026 archive (each between 0.2% and 2% of sorties); doc 16 "Pages". | it1.x | `[DECIDED]` (maintainer, 2026-10-03), placement and wording `[PROPOSED]` |
| FR-WEB-24 | **Whole-row links**: in tables, clicking most of a row opens the row's main target (player, mission or sortie), not only the name link; other links in the row keep working. | it1.x | `[DECIDED]` (maintainer, 2026-10-03) |
| FR-WEB-16 | **Light charts** where they help (for example kills per tour, sorties over time). The site is mostly tables. Built 2026-10-03 as server-rendered SVG: sorties per day on the home page, per-tour charts on the profile (doc 16, OQ-59). | built | `[PROPOSED]` |

### Maintainer decisions, 2026-10-03 (answers to OQ-38..66)
`[DECIDED]` by the maintainer unless noted. Work items they create are on the roadmap ("Decisions to apply").
- **Ratios (OQ-38):** K/D, K/L and kills per sortie/hour count **PvP air kills** specifically. A separate ground K/D may be shown, but it's
  not an important metric for ground pounders.
- **Hidden players (OQ-40):** the anonymised "Hidden player" row on mission pages and online now stays (FR-ADM-3 reads that way).
- **Local times (OQ-42..44):** use **locale-native** date/time formats (the browser's `Intl` default for the page language), not ISO-like.
  The zone is named once in the footer only; don't repeat it in headers. Date-only values use the viewer's local date. With tours,
  show when the **next tour starts** (on the tour page, else in the footer).
- **Tours on pages (OQ-45..48):** default to the **current tour**, with an **all-time** toggle. Never recompute subsets of tours; later
  maybe yearly or quarterly aggregates as extra level-2 rows. With a tour picked, the profile's sortie list shows the player's latest
  sorties **in that tour** (reframe the heading, e.g. "Sorties in October 2026"). A tour without sorties shows a notice (a flavor-text
  spot). The aircraft filter stays all-time. The aircraft list and the aircraft page's tiles follow the tour (maintainer, 2026-10-04; OQ-114).
  **Exceptions without a tour dropdown (`[PROPOSED]`):** the player search (a name search), and the Elo boards (all time). Achievements are per tour now (FR-WEB-26, doc 17).
- **Aircraft (OQ-49, 50, 65):** a top-level **Aircraft** page with a table of all aircraft; totals include hidden players and missions.
  Rank a type's top pilots by **skill (Elo or ground proficiency)**, not by volume ("not a grind"). **Cross-aircraft / per-type Elo** is
  wanted.
- **PvE (OQ-51):** the four kill rows and eight loss classes as built.
- **Ammo damage (OQ-52):** **hide the per-ammo damage** for now: follow-up damage (structural damage, then a hard turn breaks the wing) is
  hard to attribute. Keep hits. Benched for a post-release analysis. **As applied** (OQ-77, maintainer, 2026-10-04): *all* per-ammo damage is
  hidden on the sortie page, including the bombs/rockets/napalm table and the "damage no hit could be blamed on" note, not only the gun ammo
  table; damage is still stored, so reverting part of it is cheap.
- **Ammo names (OQ-76, FR-WEB-18; data in `src/il2ks/core/catalog/data/ammo.csv`)** `[DECIDED]` for now (maintainer, 2026-10-04): the name
  guesses stay. Convention: `<cartridge> <round type>` with the game's round letters (".50 BMG API", "12.7×108 mm API-T", "23×115 mm HEI-T"),
  rockets and bombs by designation and size ("HVAR 5 in", "FAB-100", "Napalm 110 gal"); the real designation (M8 API, OZT) is a tooltip; names
  are not translated. Low confidence, worth asking the IL-2 Korea developers: `BULLET_7-62_RUS_HEI` (shown "7.62×54R HEI", probably the PZ
  incendiary-tracer), `BULLET_12-7_RUS_HEI` ("12.7×108 mm HEI", probably MDZ), `BULLET_9-01_GER_FMJ` ("9 mm ball", odd attribution to
  aircraft), `SHELL_57_RUS_CV` ("57×348 mm", round type unclear). US 20 mm rounds never appear in the sample logs.
- **Flavor text (OQ-53, 47, 66):** the five spots as built; keep looking for more (empty tours, stat highlights).
- **Sortie map (OQ-54, 55):** **benched until after the release.** The maintainer wants an interactive map and will first ask the dev
  community what's available; no half-finished map in the release. The built grid version stays on its branch, unmerged.
- **Killboard (OQ-56):** a config toggle to count **assists**, **off by default**.
- **Streaks (OQ-57, 58):** the rule and the lists as built, plus a per-player tab with **the player's own best streaks**.
- **Charts (OQ-59):** start with the charts as built; revisit after the release (roadmap reminder).
- **Icons (OQ-60):** the placeholder picks are fine; doc 15's designer brief must list **every** icon file.
- **Rams (OQ-61):** test the ram signal; make sure by testing that the results are plausible before it's switched on anywhere.
- **Scores (OQ-62, 63):** air and ground scores **stay separate**, never one combined score (old il2_stats servers rewarded ground
  pounding far more). Point values as built. Penalties: **percentages of the sortie's score by outcome**, configurable, starting at
  **death 80%**, **capture 50%** and **plane lost** (without death or capture) **20%**; friendly fire and suspected early bailout stay **flat** penalties (defaults as built). OQ-67 has the
  details (built 2026-10-04 with Claude's defaults, doc 13 "Score").
- **Leaderboards (OQ-64):** highlight **Elo (jet and prop)** and **ground proficiency** most: those go on the **home page**; the other
  boards stay on the leaderboards page. Later (2026-10-04) the maintainer added **interception** and **tank busting** "as visible as Elo and
  ground score per hour" (home page: OQ-104) and fixed the board order (OQ-84).
- **Stat highlights (OQ-66):** as built; never highlight negative stats (there's no good framing). Flavor-text potential.
- **Windows service account (OQ-41):** `NT SERVICE\il2ks` virtual account; revisit if it causes trouble.
- **Heightmaps (OQ-39):** the maintainer will try to get them; until then keep only the bailout logic that works without terrain height
  (rule v3 as built).

**Follow-up answers, 2026-10-04 (batch 2, OQ-68..78)**, `[DECIDED]`; each is placed in its doc:
- **Installer and ops** (doc 07): service account gets Modify on the log folder, network folders fine (OQ-68); `/ADMINPASSWORD=` kept (OQ-69);
  no setup page in Docker, `il2ks createadmin` documented (OQ-70); `restore` refuses while the site runs (OQ-71).
- **Hall of shame** (FR-WEB-4, doc 16): the tile counts friendly-fire **kills** (a counted sortie with at least one friendly kill; OQ-72);
  "Strafed on the ground" stays in "Other totals" for now, the maintainer reviews all pages later (OQ-73); quips and the p90 rule as applied
  (OQ-74); two flavor lines replaced (OQ-75); all per-ammo damage hidden (OQ-77, above); ammo-name guesses stay (OQ-76, above).
- **Tour selector** (FR-WEB-10, TD-26, doc 16): one dropdown with "All time" and "Current tour" as its top two entries, no segmented toggle (OQ-78).

**Follow-up answers, 2026-10-04 (batches 3 and 4, OQ-79..113)**, `[DECIDED]` by the maintainer (OQ-84, 92, 97..99 are in the docs they touch); where each lives:
- **Tours** (TD-26, doc 16): the current tour is the default everywhere, the home page included (OQ-79); a tour starts with its first ingested mission (OQ-80).
- **Killboard and streaks** (FR-WEB-9, FR-WEB-25): assists received as a detail, not a column (OQ-81); a history page of all of a player's streaks, runs of 2+ sorties (OQ-82); tie-breaks as built (OQ-83).
- **Aircraft** (FR-WEB-8): an attack type has more attack than air-superiority sorties (OQ-86); 10 fights minimum for matchup ratios (OQ-110).
- **Branding** (FR-ADM-2, doc 16): nav links up to 2000 characters, new tab, 3 recommended (OQ-87); Default preset = the original military theme, themes from scratch, a simple contrast warning (OQ-88).
- **Rules** (doc 13): `credit_rams` on by default (OQ-89); a ram credits both enemies (OQ-90); bombs and rockets estimate after a loss, guns unknown (OQ-101);
  interception victims: AI and player, transports too, credited kills, air-superiority sorties, minimums as built (OQ-102); tank busting counts attack sorties only (OQ-103);
  timeline hits: 0.2% per burst, scenery excluded (OQ-108); strafed needs significant damage by another object after the landing, a damaged aircraft that fails its landing is crashed (OQ-112).
- **Config** (FR-OPS-2): old `[score]` penalty keys warn and are ignored (OQ-100). **Installer** (doc 07): the upgrade pop-up lists the templates and points to `il2ks doctor` (OQ-93).
- **Visual assets** (doc 15): no asset gates the release (OQ-94); icon gaps filled from other sets as needed, Tabler so far (OQ-95).
- **Web** (doc 16): 10 missions and 20 rows a page (OQ-96); home page: six boards in a 3x2 grid with play time (OQ-104); Elo games are "encounters" (UI wording);
  fate badges fine for now (OQ-106); Mission is an optional column on the sortie list (OQ-109); mission table defaults fine for now (OQ-113); stolen-targets quip thresholds as built (OQ-111).
- **Accepted 2026-10-04** (maintainer; the maintainer spot-checks them): aircraft stats per tour with the current tour as default, zero
  tiles for a type not flown in the tour (OQ-114); aircraft and crew as separate hit rows, capped at 100% (OQ-115), and **new**: the sortie
  page shows the pilot's remaining health and the aircraft's damage, forced to 0% health when the pilot died and 100% damage when the
  aircraft was destroyed; ammo mixes, top 10 plus a fold (OQ-116); the profile shows **only the favourite loadout** per aircraft, without
  the mod-set and hits-by-ammo detail (OQ-117, changed); front-page image display (OQ-118); quip defaults (OQ-119). Object name variants (OQ-120): "Il-10" / "IL-10" and `B 29` / `B-29` are one aircraft each (catalog spelling and aliases,
  merged on upgrade); vehicle and static pairs such as `GAZ_63` / `GAZ-63` stay separate for now. The profile's per-aircraft table keeps
  linking to the pilot's sorties in that aircraft (OQ-121).

### Score and ratings
Recorded by the maintainer on 2026-10-03; built 2026-10-04 (air and ground score, leaderboards, Elo: doc 13 "Score", doc 16). Its **inputs are computed at ingest
from iteration 1** (combat role, time on target, Elo), so pages needed no reprocess. Editing the score values (FR-ADM-7) is still the `[score]` config section. The exact rules are in
[13_game_rules.md](13_game_rules.md#combat-role-time-on-target-and-ratings).
- **Two scores, not one**: an **air score** (air-to-air) and a **ground score** (ground attack). A good ground pounder and a good dogfighter
  are different skills.
- **Ground kills keep every class**, including static objects (fences, storage stacks, parked vehicles: most server vehicles are static for
  performance). Each class gets its own score value, with trivial statics like fences worth very little. The kill breakdown by class is shown.
- **Combat role per sortie** `[DECIDED]` (OQ-27): by loadout. A sortie carrying bombs, rockets or napalm is an **attack** sortie; guns only
  (drop tanks allowed) is **air superiority**; the IL-10 is always attack. Several types fly both roles (F-51D, F-80C, F-84E, sometimes
  MiG-15bis and F-86A-5), so a fixed role per aircraft type would be wrong.
- **Air-to-air Elo** (FR-WEB-19) `[DECIDED]` (OQ-28): a rating updated per PvP kill between two **air-superiority** sorties only (attack
  sorties would be easy prey). **Separate prop and jet ratings.** A jet killing a prop changes nothing; a prop killing a jet is rewarded
  heavily (both ratings move with a double K). The K-factor and the cross-pool weight are config values, first guesses to tune with data.
  Per-aircraft-type ratings from the same games are built (`PlayerAircraft.elo`, OQ-49, doc 13).
- **Ground proficiency** (FR-WEB-20): ground score per hour **on target** for attack sorties. **Time on target** `[DECIDED]` (OQ-29): only
  ordnance (bombs, napalm, rockets) released **within 3 km (horizontal) of an enemy ground object** counts, plus 1 minute of run-in before
  the first such release. Releases far from any target (jettisoned when intercepted) don't count, and neither does transit.
- **Interception** `[DECIDED]` (maintainer, 2026-10-04, OQ-102): kills of **bombers, attackers and transports** (AI or player) per hour of **air
  superiority** flight, credited kills only, for air superiority sorties only; a pilot needs 5 such sorties and 60 minutes of that flight.
  **Tank busting** `[DECIDED]` (OQ-103): tanks destroyed in **attack** sorties per hour on target, with the ground-per-hour minimums. Both rank next to Elo and ground per hour (FR-WEB-7) and
  carry stat marks (FR-WEB-22). Rules: doc 13.
- Playable roster today (2026-10): fighters and attack aircraft only; **bombers are AI only** (a bomber expansion is announced). Jets: MiG-15bis,
  F-86A-5, F-80C-10, F-84E. Props: F-51D, Yak-9P, La-11, IL-10 (attacker). The catalog carries the prop/jet attribute.

## Server admin (FR-ADM)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-ADM-1 | Django admin, available only to admin accounts created during setup. | v1 | `[PROPOSED]` |
| FR-ADM-2 | **Branding without touching files**: site title, server name, logo, accent colors, short description and links (for example Discord), all set in the admin UI. **Logo uploads**: raster only (PNG, JPEG, WebP; **no SVG**, which can carry scripts), size-limited, and re-encoded with Pillow on upload so only clean pixels are stored. They're stored in the data directory's `media/` and served by a small Django view (WhiteNoise only serves collected static files, not uploads) with `X-Content-Type-Options: nosniff` and caching headers. SVG logos stay possible through `custom/static/`, which needs file-system access and is therefore trusted. **As built** (2026-10-03): type checked by content (PNG/JPEG/WebP), ≤ 2 MB, ≤ 25 megapixels checked before decoding, fully decoded (truncated files fail), re-encoded from pixels to PNG (metadata and any appended bytes are dropped, so a polyglot file comes out clean), downscaled to ≤ 1024 × 256, stored as `media/branding/logo-<hash>.png`; the media view serves only that folder with `nosniff`, a sandboxing CSP and a one-year immutable cache. **Theme, fonts and navigation links** (2026-10-04, replaces the single accent color and the "Links" text box): a color theme (overrides per mode for the `--il2-*` tokens, presets, contrast warnings that never block), a heading and a body font from bundled and system stacks or from an **uploaded font** (`.woff2` or `.woff`, 2 MB, up to 6 files, content-checked like the logo and self-hosted with an `@font-face` that il2ks writes itself; `SiteSettings.custom_fonts`), and an ordered list of extra navigation links (http/https only, new tab, we recommend at most 3, cap 30); how it is built is in doc 16 "Branding". `[DECIDED]` (maintainer, 2026-10-04): link addresses up to 2000 characters, as http/https only, in a new tab, we recommend 3 (OQ-87); the **Default** preset is the original military theme, a theme can be built from scratch (every token is editable) and the contrast check stays a simple warning after save (OQ-88). **Coalition emblems** (2026-10-03): each side's emblem is a site-settings choice, neutral by default, or period insignia (REDFOR: Soviet, Chinese, North Korean; BLUFOR: US, South Korean, UN-style), doc 15. | v1 | `[DECIDED]` (branding), `[PROPOSED]` (upload handling, 2026-10-02) |
| FR-ADM-3 | Hide a player (privacy request or cheater) or a mission from public pages. **Hiding is presentation only: everything is still computed, just not shown** (maintainer, 2026-10-03, OQ-35). A hidden player: gone from search and mission rosters, their profile and sortie pages answer 404 (like a non-existent ID), and other players' pages show "Hidden player" without a link; their sorties still count for everyone else. A hidden mission: gone from lists and the home page, its page and its sorties' pages answer 404, its sorties are left out of player sortie lists. Totals and Elo are not recomputed, so hiding is instant and reversible. Later, if needed: a separate "exclude from stats" switch for cheaters that removes their games from Elo and their kills from victims' deaths (needs a rebuild). | v1 | `[DECIDED]` (2026-10-03) |
| FR-ADM-4 | See ingestion status: last processed mission, errors, unknown objects, unknown event types. **As built** (2026-10-03): an "Ingestion status" page in the admin (`/admin/ingestion/`: last OK mission, latest attempt, OK/failed over 30 days, missions waiting for a retry or given up on, unknown object types, unknown event types and keys, missions completed only by the idle timeout) plus a read-only run list with filters and a detail view (error traceback, warnings, unknown ATypes and keys). Ingested rows (players, missions, counters) are read-only in the admin except `is_hidden`; object and country names are editable with a "reset to catalog default" action. | v1 | `[PROPOSED]` |
| FR-ADM-5 | Edit the object catalog (names and classes of game objects) and the display names of coalitions and countries without a new release. Game object names ship with **project-set defaults, including translations**. Admin edits are optional overrides that survive upgrades and can be reset (TD-24). | v1 (English default names); **it2**: object-name overrides (required for public release) and translations (not a release gate) | `[DECIDED]` (object-name defaults and overrides, 2026-10-02), `[PROPOSED]` (rest) |
| FR-ADM-6 | **Template and static overrides**: a `custom/` folder (in the data directory, so it survives upgrades) whose templates and static files take priority over the built-in ones. This is very important for some server owners. | v1 | `[DECIDED]` (TD-25) |
| FR-ADM-9 | **Front-page image** (maintainer, 2026-10-04, before the release): an option, off by default (the home page is unchanged when off), to show a large image such as a map of the current situation dominating the home page. The admin sets a server file path; the site picks up changes within about 10 s (mtime polling, works on Windows, Linux and network shares). The file is validated and re-encoded like the logo (content-hash name), never served directly. An embed (iframe to an interactive map) mode is designed for but not built. | release | `[DECIDED]` (feature), `[PROPOSED]` (delivery) |
| FR-ADM-7 | Edit scoring values. | later | `[DEFERRED]` (with the score concept) |
| FR-ADM-8 | Start a new tour manually (when the tour mode is manual), and rename tours. | it2 | `[PROPOSED]` |

## Operations (FR-OPS)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-OPS-1 | One command-line entry point, **`il2ks`**, with subcommands: `setup`, `ingest`, `watch`, `web`, `run` (web + watch + HTTPS proxy together), `reprocess`, `createadmin`, `doctor` (checks the configuration), `db copy`. | v1 | `[DECIDED]` (name `il2ks`), `[PROPOSED]` (subcommands) |
| FR-OPS-2 | A single, commented configuration file holding the log path, DB connection, timezone, domain/HTTPS settings, bailout rule thresholds, and (it2) tour mode. A key that was replaced or removed (the flat `[score] penalty_*` keys, `[rules] parachute_deaths`) is **ignored with a warning** at load, shown by `il2ks doctor`, never an error (maintainer, 2026-10-04, OQ-100; needs a line in the release notes). | v1 | `[DECIDED]` (ignored keys), `[PROPOSED]` (rest) |
| FR-OPS-3 | Database migrations run automatically on start or upgrade. Data fix-ups an upgrade needs (tours, scores, per-type ratings, the type killboard, interception counters, the air / ground assist split, medals) run once after the migrations (level 2 is rebuilt at most once for all of them) and are recorded in `SiteSettings.backfills_done`, so a database that legitimately looks "unfilled" is not rebuilt after every later migration (2026-10-04, `ops/migrate.py`, doc 14). | v1 | `[PROPOSED]` |
| FR-OPS-6 | **Backups of admin state.** Archives can rebuild stats, but not hidden players, name overrides, branding, admin accounts, or the server ID. `il2ks backup` writes a consistent snapshot (SQLite online backup / `VACUUM INTO`) plus the config file and `custom/` into a dated zip in the data directory, keeping the last N. It runs **automatically before every migration/upgrade** and daily by default. `il2ks restore <zip>` reverses it. The server ID also lives in the config file. | v1 | `[PROPOSED]` (2026-10-02 review) |
| FR-OPS-4 | **HTTPS only.** The site is served over HTTPS, and plain HTTP only redirects (TD-23). | v1 | `[DECIDED]` |
| FR-OPS-5 | **One install per game server.** Several installs on the same machine (the rare multi-DServer case) must coexist: separate data directories, ports, and service names, with nothing hard-coded. | v1 | `[DECIDED]` |
