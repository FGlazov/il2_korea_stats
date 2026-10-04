# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78 and OQ-79..113 right after it, each with the doc that holds the rule).


## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Needs the maintainer's review

**OQ-114 Aircraft stats per tour: defaults** (built 2026-10-04 on the maintainer's request)
Defaults applied: `/aircraft/` and `/aircraft/<pk>/` default to the **current tour** like every other page (TD-26), which changes what
existing links to an aircraft page show; a tour with no flights shows "No aircraft has flown in this tour yet."; a type not flown in
the selected tour shows zero tiles, not a 404; top pilots, hits to destroy, loadouts and the side badge stay all time (noted on the page).

**OQ-115 Timeline hits: aircraft and crew** (fix of the "+200%" rows, 2026-10-04)
Defaults applied: the aircraft and its pilot/crew are separate hit rows; every bot (pilot, gunners, AI pilots) counts as crew, labelled
"Pilot / crew"; damage capped at 100% per row (doc 13 "Timeline hits").

**OQ-116 Ammo mixes on the aircraft page** (built 2026-10-04 on the maintainer's request, "like il2_stats")
Defaults applied: the "Hits to destroy" count column is called "Instances" (same numbers, the intro explains it); the top 10 mixes are
shown with per-ammo average hits, the rest folded; per-ammo average = that ammo's hits / the mix's instances; mixes are all time.
Existing databases need `il2ks reprocess --all` (level-1 rows).

## Lower impact (owner: maintainer, outside input)

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Progress (2026-10-04): the maintainer supplied `korea_weapon_mods.csv` (mod names; hypothesis: WM bit 0 always set, mod k = bit k) and a
newer payload table (it replaces the old one for all sorties). Still open: confirm the bit hypothesis on real logs.
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
