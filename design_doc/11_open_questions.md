# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78, OQ-79..113, OQ-114..123 and OQ-124..130 right after it, each with the doc that holds the rule).


## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Needs the maintainer's review

**OQ-131 New tour on a decisive mission: the rule** (built 2026-10-05, `/admin/tours/` "Tour options", off by default)
Evidence (doc 12, 210 sample missions): the only result event is AType 8 (all `TYPE:0`); 131 missions (62%) have one side reporting
`RES:1`, 74 (35%) are draws (both sides `RES:1`: 22, or neither: 52), 5 have no result. Defaults applied: a lone coalition completing
the objective wins; both or neither is a draw; when the option is on, a new tour starts right after a won mission, **on top of** the
`[tours]` mode (monthly / days / manual: manual gives "only wins start tours"); later parts of a tour are titled "October 2026 (2)",
"(3)"…; the cuts use every mission in the database (one campaign per site). Missions show "Won by …", "Draw" or a dash. Fixed on the way:
the old rule credited a win to the first side in the 22 both-completed missions. Existing missions need `il2ks reprocess` for the result.

**OQ-132 Flight-time score defaults** (built 2026-10-05, `/admin/score/`, off by default)
Defaults applied: 1 point per hour in the air (range 0–100; an AI air kill is 2, a player air kill 10), on the air score of every pilot
sortie whatever its role (attack sorties too), reduced by the outcome percentage like kill points, gunners get none.

## Lower impact (owner: maintainer, outside input)

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Progress (2026-10-04): the maintainer supplied the mod names (`weapon_mods.csv`; WM bit 0 always set, mod k = bit k, verified on
30.7k spawns) and a newer payload table (it replaces the old one for all sorties). Still open: unknown payloads after game updates.
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
