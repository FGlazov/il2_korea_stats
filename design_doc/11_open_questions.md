# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78, OQ-79..113, OQ-114..123 and OQ-124..132 right after it, each with the doc that holds the rule).


## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Needs the maintainer's review

Nothing waits for the maintainer's review.

## Lower impact (owner: maintainer, outside input)

**OQ-134 Side balance: what is "the side with fewer players"?** (owner: maintainer; roadmap "Next version", side balance)
Proposed: decided at spawn in the replay (the pilot's side has strictly fewer pilots in open sorties at that tick; equal = not underdog).
Alternative: compare the mission's total pilots per side, which is simpler but rewards joining the side that fills up later. Also: should the
underdog share get a stat mark and a quip, or stay a plain figure in "Other totals"?

**OQ-135 Altitude units on the timeline** (owner: maintainer; roadmap "Next version", altitude)
Proposed: BLUFOR in feet as "Angels N" (thousands of feet; exact feet in the tooltip), REDFOR in metres ("4 500 m"; Soviet altimeters were
metric, kilometres only in speech, so not km). Confirm, or pick one unit for everyone (the viewer's language is another option: feet for
English, metres elsewhere).

**OQ-133 Markdown pages: sources and images** (owner: maintainer; roadmap "Markdown pages in the navigation")
Which sources should a page accept: Markdown typed in the admin, a polled http/https URL, a file on the server machine,
or several of these? Should images on a remote source be copied to the server (proposed: yes, re-encoded, no hotlinking
of third-party hosts) or linked as they are? Is one language per page enough, or does each page need a version per site
language?

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
