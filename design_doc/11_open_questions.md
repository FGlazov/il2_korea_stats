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

**OQ-67 Percentage penalties: the details** (OQ-63 follow-up; (a) answered: a plane lost without death or capture costs 20%)
Claude's defaults otherwise: the percentage is taken from **both** the sortie's air and ground score, only from positive scores (a
percentage never makes a score negative), and death + capture together apply the larger one only.

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

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
