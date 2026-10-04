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

OQ-38 and OQ-40..66 are answered (maintainer, 2026-10-03): see doc 02 "Maintainer decisions, 2026-10-03".

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

**OQ-67 Percentage penalties: the details** `[DECIDED]`, Claude's defaults applied and built (2026-10-04; the maintainer may still change them)
(OQ-63 follow-up; (a) answered: a plane lost without death or capture costs 20%.) As built (doc 13 "Score"): death 80%, capture 50%, plane lost
without death or capture 20% (`[score] penalty_*_pct`, clamped 0..100; the old flat keys are gone). The percentage is taken from **both** the sortie's
air and ground score, only from positive scores (a percentage never makes a score negative), and when several apply the **largest** one counts
(death and capture are not added); the flat penalties (suspected early bailout 5, friendly kill 3 up to 5 per sortie) come off afterwards from
the combat role's score. Delete this entry once the maintainer has seen it.

### Decisions made during the 2026-10-04 run (defaults applied; answer by ID)

**OQ-68 Installer: grant the service account access to the game log folder**
The service now runs as `NT SERVICE\il2ks` (OQ-41), which can't read the game's log folder the way SYSTEM could. Default applied: the
installer grants it Modify on the log folder chosen in the wizard (read access, plus `after_archive = move`). This changes ACLs outside
il2ks's own folders. Network-drive log folders still don't work under the service.

**OQ-69 Installer: keep `/ADMINPASSWORD=` for silent installs?**
Default applied: kept for compatibility, documented as visible in process lists and the installer log; the new `/ADMINPASSWORDFILE=`
(the file is deleted after use) is the recommended switch. Alternative: remove `/ADMINPASSWORD=`.

**OQ-70 Docker: no browser setup page**
A container never sees a loopback peer, so the setup page can't work there. Default applied: the image disables it
(`IL2KS_SETUP_PAGE=off`) and the logs explain `il2ks createadmin` when no admin exists.

**OQ-71 `restore` refuses while the site runs**
Default applied: `il2ks restore` exits with code 3 when `il2ks run` holds its lock or the web port answers; `--force` overrides. Scripts that
restored against a live site now need `--force`.

**OQ-72 Hall of shame: what counts as a friendly-fire incident**
Default applied: a counted pilot sortie with at least one friendly kill (`friendly_fire_incidents`, on every counters table). Hits and damage
alone don't count (collateral hits on own-side objects would swamp the tile); this matches the sortie-page badge.

**OQ-73 Hall of shame: where "Strafed on the ground" went**
Default applied: the "Other totals" list on the profile (it isn't the pilot's own fault and sits next to friendly kills and hits). The
sortie-page badge stays.

**OQ-74 Hall of shame: quips and the p90 rule**
Default applied: one quip spot per case (taxi only, friendly fire only, both, none), 3–4 variants each, written by Claude; a separate,
warmer variant when a rate per sortie is strictly above the 90th percentile of pilots with at least `[marks] min_sorties` sorties in the
same scope (all time or the selected tour). Only the elevated kind is named; no ranking is shown. The lines need the maintainer's review
(`src/il2ks/web/flavor.py`).

**OQ-75 Two flavor lines replaced**
As asked (2026-10-03), the POW "food" joke and "Ace-in-a-day territory" are gone. Replacements: "Out of the fight, but not out of the
story." (sortie captured) and "Best showing of the mission. Well flown." (top pilot).

**OQ-76 Ammo names: convention and the uncertain ones** (FR-WEB-18; data in `src/il2ks/core/catalog/data/ammo.csv`)
Default applied: `<cartridge> <round type>` with the game's round letters (".50 BMG API", "12.7×108 mm API-T", "23×115 mm HEI-T"),
rockets and bombs by designation and size ("HVAR 5 in", "FAB-100", "Napalm 110 gal"); the real designation (M8 API, OZT) is a tooltip;
names are not translated. Low confidence, worth asking the IL-2 Korea developers: `BULLET_7-62_RUS_HEI` (shown "7.62×54R HEI", probably
the PZ incendiary-tracer), `BULLET_12-7_RUS_HEI` ("12.7×108 mm HEI", probably MDZ), `BULLET_9-01_GER_FMJ` ("9 mm ball", odd attribution
to aircraft), `SHELL_57_RUS_CV` ("57×348 mm", round type unclear). US 20 mm rounds never appear in the sample logs.

**OQ-77 Ammo: which damage columns to hide** (OQ-52 follow-up)
Default applied: all per-ammo damage is hidden on the sortie page, including the bombs/rockets/napalm table and the "damage no hit
could be blamed on" note, not only the gun ammo table. Damage is still stored. Reverting part of it is cheap.

**OQ-78 Tour selector: segmented toggle and the "next tour starts" line**
Default applied: a segmented toggle (current tour / All time) in addition to the tour select, and the "Next tour starts <local time>" line
beside the toggle, not in the footer (it is hidden in manual mode, and by JS once the moment has passed). Alternative: the select alone, and
the line in the footer as OQ-44 first said.

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

**OQ-84 Leaderboards: fighter and attack as grouped tabs, prop and jet as a filter**
Default applied: tabs grouped as Fighters (air score, Elo prop, Elo jet), Attack (ground score, ground per hour) and General (kills);
`?pool=prop|jet` on the score and kill boards only (Elo boards have no pool filter, a chosen aircraft type overrides the pool). Alternative:
separate prop and jet boards as their own tabs, or one flat list of tabs.

**OQ-85 Home page: which boards and how many**
Default applied: the top 5 of Elo jet, Elo prop and ground per hour, all time (not the current tour), plus "Online now". Alternative: the
current tour's top 5, more rows, or the air and ground score boards on the home page.

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

**OQ-91 Parachute deaths off: what happens to the shooter's kill**
Default applied: with `parachute_deaths = false` the pilot killed after a bailout is not a death, and the shooter's kill of that pilot is
removed too (the aircraft was already lost and credited). Alternative: keep the shooter's kill and only spare the victim's death.

**OQ-92 Ram detection thresholds**
Default applied: 2 s and 50 m (`ram_window_s`, `ram_distance_m`). Tightening to 0.5 s / 15 m drops the 4 looser cases of the 17 rams in the samples. Alternative: the tighter values.

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

**OQ-97 Page performance tests: in every test job**
Default applied: the `perf` tests (about 40 s of seeding each) run in every test job instead of a job of their own. Alternative: a separate CI job,
or run only on SQLite/ubuntu.

**OQ-98 Aircraft stats store four ratio fractions**
`AircraftStats.kd`, `kl`, `survival` and `attack_share` are columns, which departs from "don't store ratios" (TD-22, maintainer 2026-10-02): the
aircraft list sorts and paginates in SQL, which needs a column to sort by. Default applied: keep the columns as the one documented exception
(doc 06). Alternative: compute ratios in the view and sort the (few dozen) rows in Python, which keeps TD-22 literal.

**OQ-99 Parachute deaths off: a pilot killed in the parachute can become "captured"**
With `[rules] parachute_deaths = false` the pilot killed after a bailout is not a death, so the pilot's final position decides capture: over enemy
territory the sortie becomes captured (50% score penalty, streak broken). Default applied: that behaviour (the pilot survived, the capture rules
apply). Alternative: treat such pilots as neither dead nor captured.

**OQ-100 Old `[score]` penalty keys are replaced**
The flat `penalty_death`, `penalty_plane_lost` and `penalty_capture` keys became `penalty_death_pct`, `penalty_plane_lost_pct` and
`penalty_capture_pct` (percent). Default applied: the old keys are ignored (a warning at config load is being added), so a server that set them
gets the new defaults until it edits `il2ks.toml`; needs a line in the release notes. Alternative: convert old values automatically, or fail the
config load.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
