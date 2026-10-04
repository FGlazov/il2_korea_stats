# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78 right after it).

## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Defaults applied by agents, 2026-10-04 (answer by ID)

**OQ-79 Tours: the current tour is the default view on the leaderboards and killboard pages too**
Default applied: every page with a tour selector (profile, mission list, player's sortie list, killboard, leaderboards) opens on the current
tour when `?tour` is absent or unknown; `?tour=all` is all time. Alternative: all time on the leaderboards and the killboard (the "all-time
rankings" view people may expect when they arrive from a link).

**OQ-80 Tours: the first day of a new month**
Default applied: the current tour is the newest `Tour` row, which only exists once a mission of the new tour is ingested, so the first day of a new
month keeps showing the previous tour until that first mission arrives (no empty page on the 1st). Alternative: switch at midnight of the
tour boundary and show the empty-tour flavor text until the first mission.

**OQ-81 Killboard: what an assist is and where it counts**
Default applied (`[killboard] assists`, off by default): an assist is the player's assist credit on the opponent's sortie, in one column (no
"assists received"); a pair with only assists appears when the toggle is on, and its "last encounter" counts assists too; hidden opponents sort
last. Alternative: also show assists received, or show only pairs with a kill or a death.

**OQ-82 Streaks: what the per-tour and best-streak views show**
Default applied: best streaks are a sub-page of the player (`/players/<id>/streaks/`), not a tab on the profile; the per-tour view shows the best
streak only (no current streak, since a running streak crosses tours); the "air kills" best row is omitted when the best such streak has no air
kill. Alternative: a profile tab; a current streak per tour; show a zero row.

**OQ-83 Streaks: tie-breaks between equal best streaks**
Default applied: by air kills, more sorties, then more flight time, then the earlier one; by flight time, more sorties, then more air kills,
then the earlier one. Alternative: prefer the more recent
streak, or list ties side by side.

**OQ-86 An aircraft type's top pilots: when the ground ranking comes first**
Default applied: a type with an attack share of 50% or more lists the ground ranking (ground score per hour on target) first, otherwise the
Elo ranking first; both under the leaderboard minimums. Alternative: a different share threshold, or always the same order.

**OQ-87 Navigation links: how they open and how many**
Default applied: links open in a new tab (`rel="noopener noreferrer"`); only http/https URLs (old `mailto:` and relative links were dropped in the
migration); we recommend at most 3 short-labelled links (measured: 3 fit at >= 1280 px, 5 at 768 px, 2 at 360 px) and cap the list at 30; order
by a position number, no drag and drop. Alternative: same tab, allow `mailto:`, drag and drop ordering, a "more" menu for overflow.

**OQ-88 Theme: presets, contrast and what stays fixed**
Default applied: presets Steel blue, Desert sand, High contrast and Default; the admin warns on WCAG contrast below 4.5:1 (charts 3:1) but never
blocks a save; the camo pattern and the favicon stay non-themable images; the setup page's "ok" green is now the badge green. Alternative:
block low-contrast themes, theme the camo tile, fewer or more presets.

**OQ-89 Rams: should `credit_rams` be on by default?**
Default applied: `credit_rams = false` (nobody is credited, as before). The validation on 210 missions (17 rams, 13 between enemies, 26 kills, no
false positive identified) supports turning it on, and the agent recommends enabling it. Alternative: default true. Applies via `il2ks reprocess --all`.

**OQ-90 Rams: their own kind in the UI**
Default applied: a ram kill is an ordinary kill (`attacker` / `shot_down`, `via direct`); nothing says "rammed". Alternative: a `ram` value of
`KillVia` and a "rammed by" label on the sortie page and the timeline (needs a migration and a reprocess).

**OQ-93 Installer: what the upgrade check reports**
Default applied: on an upgrade the installer reports only customized overrides (not other doctor findings); every problem state (outdated, newer,
unversioned, orphan) triggers the message box, which has an OK button only; silent installs write to the installer log and
`<data>\logs\installer-custom-check.log`; it never fails the install. Alternative: also run the doctor, or show the box only for "outdated".

**OQ-94 Visual assets: which images gate the release** (doc 15)
Default applied: the final `og-default.png` and the PNG favicon set are marked not required (the site works without them) although the designer
brief rates them P1. Alternative: make them release gates.

**OQ-95 Visual assets: icon gaps** (doc 15)
Default applied: no tiles for bailouts and captures are added; `mission-ended` has no outcome value (the icon stays unused); the own-drawn
`friendly-fire` stat icon stays as a placeholder. Alternative: add the two tiles or drop the icons from the brief; give `mission-ended` an
outcome value or drop it; commission a proper friendly-fire icon.

**OQ-96 Page weight of mission and sortie pages**
Real-log mission and sortie pages are 107 to 122 KB of HTML (other pages about 20 KB). Default applied: the HTML budget is 150 KB
(NFR-PERF-6) and the lists are not trimmed or paginated. Alternative: paginate or trim the sortie lists on those pages.

**OQ-100 Old `[score]` penalty keys are replaced**
The flat `penalty_death`, `penalty_plane_lost` and `penalty_capture` keys became `penalty_death_pct`, `penalty_plane_lost_pct` and
`penalty_capture_pct` (percent). Default applied: the old keys are ignored with a warning at config load (logged, shown by `il2ks doctor`), so a server that set them
gets the new defaults until it edits `il2ks.toml`; needs a line in the release notes. Alternative: convert old values automatically, or fail the
config load.

**OQ-101 Bombs and rockets used after a loss: show an estimate?** (built as the default, doc 13 "Ammo and resupply")
After a bailout or other late sortie end the "ammo left" record can't be trusted. Release events are release *commands* (a pair of bombs
often leaves in one event; a rocket event is a salvo), so they don't give exact counts. Default applied: no release event in the sortie means
0 used (right in ~87–90% of comparable sorties); one or more releases leave "used" unknown. Alternative: show "~all loaded" as an estimate
(marked "~" with a tooltip) when at least one release happened; it matches the trusted record in 92% of bomb and 87% of rocket sorties and would
fill ~970 bomb and ~450 rocket sorties in the samples.

### Second run, 2026-10-04

**OQ-102 Interception: who counts as a victim, and the minimum**
Default applied (doc 13 "Interception and tank busting"): the victim is a **bomber or attacker** by catalog class (AI B-29, Tu-2, ...), or a **player
sortie with the combat role attack** (a fighter with bombs or rockets is attacking); transports (C-47B, Li-2) are not victims here, although the
"bomber hunter" quip counts them. Only credited kills count (no assists, no friendly), and only those made in **air superiority** sorties, per hour
of air superiority flight. A pilot needs **5 air superiority sorties and 60 minutes** of that flight (`[score] min_air_superiority_sorties`,
`min_air_superiority_minutes`). Alternatives: include transports; count the attackers shot down from attack sorties too; 120 minutes (fewer pilots
qualify, steadier rates).

**OQ-103 Tank busting: attack sorties only**
Default applied: tanks destroyed (class `tank`, static or moving) **in attack sorties** per hour on target, so a tank shot up by an air superiority
sortie counts for nothing here, and the denominator is the same time on target as the ground-per-hour board. Minimum: 5 attack sorties and 10 minutes
on target (the ground-per-hour minimums). Alternative: every tank kill over all flight time, or only moving tanks.

**OQ-104 Home page: five boards**
Default applied: the top 5 of **Elo jet, Elo prop, interception, ground score per hour and tank busting**, all time (the maintainer asked for the
skill boards to be as visible as Elo, 2026-10-04); air score and ground score stay on the leaderboards page. Each board title links to its page, the
skill boards with `?tour=all`. Alternative: fewer boards on the home page, or the current tour's top 5.

**OQ-105 Achievements: names, tiers and thresholds**
Default applied: the 12 achievements, their names, tier counts and thresholds as listed in [17](17_achievements.md) (its "Decisions" section holds the
product calls). Not repeated here. Needs the maintainer's review of every name and number.

**OQ-106 Pilot fate badge: colours and wording**
Default applied (maintainer asked for Dead / Captured / Survived, 2026-10-04): **Dead** red with the dead icon, **Captured** orange with the captured
icon, **Survived** green with no icon; dead beats captured beats survived, and an unknown fate reads Survived. The stored fate (bailed out, exited on
ground, left the server) is a tooltip on the lists and a note in brackets on the sortie page; nothing is shown for "in aircraft". The older stored-fate
badges (amber "Bailed out", purple "Left the server") are no longer used on the lists. Alternative: other colours (captured purple like the status
badge), or show the stored fate as the headline again.

**OQ-108 Timeline hits: the 0.2% burst threshold and what is left out**
The maintainer suggested "e.g. over 0.1%" per hit. Default applied: damage lines of one attacker on one target form a burst while each is within 3 s of
the last (a burst lasts at most 15 s); a burst needs **0.2% damage in total** (`[replay] hit_min_damage`, 0.002) to become a timeline row, because
strafing an airfield produces thousands of tiny lines; **static scenery** (fences, tents, stacks) as a target never gives a row; at most 150 rows per
sortie (the heaviest). Alternative: 0.1%, scenery included, or a per-line rather than per-burst threshold. Applies with `il2ks reprocess --all`.

**OQ-109 Mission is an optional column on the sortie lists**
Default applied (maintainer, 2026-10-04: "Mission is no longer a default column"): on the player's sortie list the mission is one of the optional
columns (`?cols=mission`); the default shows the time, aircraft, role, outcome, fate, damage taken, kills, assists and flight time. The sortie page and
the mission page still name the mission. Alternative: keep Mission as a default column on wide screens.

**OQ-111 Stolen-targets quip (ground assists): the thresholds**
Default applied (2026-10-04, Claude): the line fires for a pilot with **5+ ground assists, at least as many as the sortie's own ground kills, and fewer than 70 ground kills**
(70+ is the ground-pounder line). Read off the September 2026 archive: 5 is the p75 and 10 the p90 of the 1,684 sorties (11%) with any ground assist;
the line fires on 1.5% of attack sorties (0.8% of all). Ground assists score nothing and appear only in the profile's air-to-ground part and on the
sortie page. Alternative: 10+, a share of all ground damage instead of the count, or no ground line at all.

**OQ-112 Strafed on the ground: the edge cases**
Default applied (maintainer, 2026-10-04, for the headline rule; Claude for the edges): a landed aircraft (an AType 6 strictly before the loss, no takeoff
since) destroyed by an attacker is **strafed even after air damage**; an aircraft that never took off is strafed whatever hit it. Not strafed: a
**crash-landing** (shot down), and a landed wreck that **burned down with no attacker line after the landing** (shot down). A landed aircraft hit by
an attacker only before it landed and destroyed with no attacker named on the kill line counts as shot down too. The quip is `sortie_strafed_landed`
when the sortie has a landing, else `sortie_strafed` (parked); both sit after "ditched" and before "flak". Samples: 28 sorties (10 parked, 18 after
landing). Alternative: count a burned-down wreck as strafed, or one quip for both.

**OQ-113 Mission page: the default columns of the sortie tables**
Default applied (Claude, 2026-10-04; the maintainer asked for sortable tables with optional columns): the default columns stay as before (time,
pilot, aircraft, role, outcome, fate, air and ground kills, assists, flight time); `?cols=` adds damage taken and the sortie list's other extras
(not Mission, which is this page); one `?sort=` orders all three tables (the two coalitions and the others), ties by spawn time, NULLs and
gunners' empty cells last, hidden players' anonymised rows last. The PvP kill list is not sortable. Alternative: damage taken as a default
column, sort per table, or hidden players sorted in by their numbers.

**OQ-110 Aircraft matchups: the minimum of 10 fights and the "intercept flights only" filter**
Default applied: a matchup row on the aircraft page shows its exchange share (kills out of kills plus losses) and can be named best or worst only with
**at least 10** PvP air kills plus losses in the chosen scope (tour or all time); fewer shows the counts only. The filter "Intercept flights only"
keeps kills where **both** sorties had the combat role air superiority (fighter against fighter); it is a different thing from the interception
board (OQ-102), which the roadmap's wording shares. Alternative: another minimum, or rename the filter ("Fighter vs fighter").

## Lower impact (owner: maintainer, outside input)

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
