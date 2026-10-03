# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**

The iteration 1 implementation batch (`OQ-I1-*`, 2026-10-03) is resolved: game rules are in [13_game_rules.md](13_game_rules.md), parser,
catalog, ingest jobs and persistence in [14_ingest_internals.md](14_ingest_internals.md). Those IDs are retired.

OQ-27..32 (2026-10-03) are answered: combat role, Elo and time on target are in [13_game_rules.md](13_game_rules.md#combat-role-time-on-target-and-ratings),
the post-end window and ground losses in doc 13's sortie scope and outcome sections, the gunner credit rule (deferred) in FR-WEB-14.

## Frontend (iteration 1, part 2)

OQ-33 (static ground kills) is answered: a ground-kill breakdown by category, FR-WEB-4.

OQ-34 (insignia) is answered: neutral emblems by default, real insignia per country later (doc 15).

OQ-35 (hiding) is answered: compute everything, don't show it (FR-ADM-3).

OQ-36 (destruction before a disconnect is a normal loss, doc 13) and OQ-37 (ship Tabler Icons, doc 15) are answered.

**OQ-38 Should K/D and K/L count air kills only?** (Claude's default applied unless you object)
The profile's K/D (kills per death), K/L (kills per plane lost) and kills per sortie / per flight hour use **air kills only**, because
ground kills are dominated by static objects (91.6%) and would make attack pilots' ratios meaningless; ground kills get their own "per
sortie" figure. Alternative: count air + non-static ground kills, or show a separate ground K/D.

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (doc 13, bailout rule v3 candidates)
Rufus's rule checks the pilot's teardown height against a **heightmap**. il2ks has no terrain data for the Korea maps. Can Rufus (or the game
files) provide heightmaps, and under what licence can they ship in the repo? Until then the height arm is approximated or left out.

**OQ-40 Hidden players on mission pages: anonymised row or left out?** (Claude's default applied unless you object)
FR-ADM-3 says a hidden player is "gone from mission rosters", but the mission page and the online-now list show a **"Hidden player" row**
(aircraft, side, kills, no name, no links), so the mission's numbers still add up. The top-pilots lists leave them out. Default: keep the
anonymised row and reword FR-ADM-3. Alternative: drop the row (totals then don't add up).

**OQ-41 Windows service account** (Claude's default applied unless you object)
The installer ran the service as LocalSystem; the Opus review found a privilege-escalation path (a standard user can pre-create
`C:\ProgramData\il2ks` and plant code that then runs as SYSTEM). Default: run as the virtual account **`NT SERVICE\il2ks`** with rights on
the data folder only and read rights on the log folder, take ownership of the data folder on install, and start Python with `-P`. A log
folder on a **network share** then needs the share to grant the computer account read access (as with LocalSystem). Alternative: a
dedicated local user chosen in the wizard.

**OQ-42 Local time format** (FR-WEB-17; Claude's default applied unless you object)
Converted times show as `2026-09-19 22:34` (24 h, ISO-like) in every language, matching the UTC text and sorting the same way.
Alternative: locale-native formats (`19.09.2026 22:34` in German, `9/19/2026, 10:34 PM` in US English).

**OQ-43 Where the time zone is named** (FR-WEB-17; default applied)
Column headers say "Date" (no longer "Date (UTC)"); the footer says "Times are shown in your time zone (Europe/Berlin)." once converted,
"Times are shown in UTC." without JavaScript; standalone times keep a " UTC" suffix only in the no-JS text. Alternative: a zone label in
every header, or a small UTC/local toggle.

**OQ-44 Dates near midnight** (FR-WEB-17; default applied)
A date-only value shows the viewer's local date, which can differ from the UTC date (a mission at 23:30 UTC is "tomorrow" in Tokyo). Tour
membership doesn't change (it follows TD-15/TD-26). Alternative: keep date-only values in UTC.

**OQ-45 Profile default: all time or the current tour?** (tours on pages; default applied)
The profile, sortie list and mission list show **all time** unless a tour is picked (keeps the canonical URL). Alternative: open on the
current tour, with "All time" one click away.

**OQ-46 Recent sorties follow the tour** (default applied)
With a tour picked, the profile's recent-sorties list and its "All sorties" link follow that tour. Alternative: always show the latest
sorties.

**OQ-47 Tour with no sorties** (default applied)
A player with no sorties in the picked tour sees a "No sorties in this tour" notice instead of a page of zeros.

**OQ-48 Aircraft filter in a tour** (default applied)
The sortie list's aircraft choices stay all-time (every type the player ever flew), even with a tour picked. Alternative: only types
flown in that tour.

**OQ-49 Where "hits to destroy" lives** (FR-WEB-18; default applied)
A new top-level **Aircraft** page (`/aircraft/`, in the nav, linked from the sortie ammo section) lists aircraft types by counted kills
with average gun hits to destroy and a per-ammo breakdown. Meant to grow into the aircraft stats page (FR-WEB-8). Alternative: a section
on the sortie page only.

**OQ-50 Aircraft page counts hidden players and missions** (default applied)
The aircraft page is all-time and names no player, so hidden missions and players still count (hiding is presentation only, FR-ADM-3).

**OQ-51 PvE kill classes on the profile** (FR-WEB-21; default applied)
"Kills by victim" has four rows: player aircraft, AI aircraft (incl. AI gunners), anti-aircraft, other ground. Losses use all eight cause
classes (no attacker, player, AAA, ground, AI aircraft, AI gunner, friendly, unknown). The no-attacker class reads "No attacker (crash,
terrain, accident)". Alternative: split AI-gunner and ground-vehicle kills too (needs new counters).

**OQ-52 Damage units on the sortie page** (FR-WEB-18; default applied)
Damage per ammo/ordnance is shown in health units (1.0 = one whole object, 2 decimals under 10), summed over all objects hit.
Alternative: percent of one aircraft, or hide damage and show hits only. The ordnance section starts open; the profile PvE section starts
collapsed like the ground-kill breakdown.

**OQ-53 Flavor text: where and what** (FR-WEB-23; default applied)
Five spots, 3–5 variants each (doc 16 lists the rules): the hall of shame (incidents / clean sheet), a line under the home page's top
pilots (generic, never names the pilot), the home page when nobody scored, and one line on notable sorties (taxi accident, friendly fire,
captured, ditched, shot down by AA, 3+ air kills, landed badly damaged). Nothing on the mission list or most empty states. The wording
is in `src/il2ks/web/flavor.py`; Claude dropped two drafts (a prisoner-of-war joke and an "ace in a day" line that showed for one kill).
Tell me which spots or lines to cut, and whether you want more spots.

**OQ-54 What the sortie map draws** (FR-WEB-12; default applied)
Spawn, takeoff, kills, friendly fire, the loss (shot down / destroyed / killed / died), bailout, disconnect, landing and sortie end. No
assist markers (they're someone else's kill) and no "got hit" markers (hits aren't in the timeline). A run of 3+ ground kills is one
marker ("N ground kills, marked where the first fell"), like the timeline. Overlapping markers are nudged apart (tooltips keep the true
coordinates). The section is hidden when no event has a position.

**OQ-55 Map image and bounds** (doc 15, after the release)
The sortie map is a plain grid until there's a map image. The designer (or the game files) needs to provide the Korea map picture and its
exact game-coordinate bounds (`maps.json`); positions in the samples reach about 468 km. Is the Korea map one map id, or several?

**OQ-56 Killboard rules** (FR-WEB-9; default applied)
Counts kill credits only (no assists), PvP between two different accounts' **pilot** sorties (no gunners, no AI, no friendly fire), all
time. A hidden opponent shows as "Hidden player" (the pair still counts). The profile shows the top 5 each way when there's at least one
PvP encounter. Alternatives: count assists separately; per-tour killboards (easy to add).

**OQ-57 Ironman streak rules** (default applied)
A streak is consecutive pilot sorties without **death or capture**. Losing the aircraft and surviving (bailout, ditching) keeps it going;
disconnects without a death and mission-end cut-offs count as survived; sorties that never took off are skipped (unless the pilot died or
was captured). The fatal sortie isn't part of the streak (its kills are lost). Best streak = most sorties (ties: more air kills, more
flight time). All-time only. Alternative: a stricter "virtual life" where losing the aircraft also ends it.

**OQ-58 Where streaks are listed** (default applied)
A `/streaks/` page plus a "Longest ironman streaks" block of 5 on the home page, listing only streaks still running whose last sortie was
within 30 days. The profile always shows current and best.

**OQ-59 Which charts** (FR-WEB-16; default applied)
Home: **sorties per day**, last 30 days up to the newest day with data (pilots and missions per day only in the table under it). Profile:
**sorties per tour** and **air kills and deaths per tour**, shown with at least 2 tours, last 12 tours. No weekly per-player chart (needs
another table). Hidden players count in the daily pilot numbers; hidden missions don't.

**OQ-60 Placeholder icon picks** (OQ-37 applied; default applied, replaced by the designer's set later)
Air superiority = crossed swords, attack = bomb; taxi accidents = car crash, strafed = target; ditched = ripple, shot down = plane-off,
dead = cross, deaths = skull; neutral emblems: star (REDFOR) and shield (BLUFOR) as before (the star may read as Soviet). Full table in
`src/il2ks/web/static/il2ks/img/README.md`.

**OQ-61 Rams and parachute deaths** (rule toggles, work in progress; defaults keep today's behaviour)
A **ram** has no attacker in the log. Candidate signal (found in the samples, about 20 cases): two airborne aircraft destroyed within 2 s
and 50 m of each other with no gun hits between them (mutual damage in one tick is not a ram signal: 92 of 95 cases have bullet hits). Config
`[rules] credit_rams` (default off): credit each aircraft of the pair with a kill on the other? Or credit only one, and how does the
rammer's own loss count? `[rules] parachute_deaths` (default on): when off, a pilot killed after leaving the aircraft doesn't count as a
death. Does Rufus know a better ram signal?

**OQ-62 Score point values** (FR-WEB-7; first guesses from the samples, all configurable in `[score]`)
Air: PvP kill 10, AI kill 2, assist 3. Ground per category: tank 6, vehicle 3, artillery 5, AAA 5, ship 8, train 4, building 2, parked
aircraft 3, other statics (fences…) 0.2. Static vs moving isn't scored separately.

**OQ-63 Score penalties** (default applied)
Death 3, plane lost 2 (on top of a death), capture 2, suspected early bailout 5, friendly kill 3 each, capped at 5 per sortie (one sample
sortie had 159 friendly kills, statics included). Penalties come off the score of the sortie's role (attack → ground score, otherwise air
score); totals can go negative.

**OQ-64 Leaderboards** (default applied)
Boards: air score (the default), ground score, ground score per hour on target, kills, Elo prop, Elo jet; 25 per page; Elo all-time only.
Minimums to appear: 5 sorties; 5 rated Elo games (with 10, only 5 prop and 26 jet pilots qualified on the samples); 5 attack sorties and
10 min on target for the per-hour board (its rates are high-variance, dominated by statics).

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
