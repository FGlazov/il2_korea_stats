# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**

The iteration 1 implementation batch (`OQ-I1-*`, 2026-10-03) is resolved: game rules are in [13_game_rules.md](13_game_rules.md), parser,
catalog, ingest jobs and persistence in [14_ingest_internals.md](14_ingest_internals.md). Those IDs are retired.

## Needs a decision soon (iteration 1)

**OQ-30 Post-end destroy window: keep 5 s?**
You asked for ~5 minutes to absorb server lag. On all 210 sample missions, every kill line that came after a normal sortie end came within
**1 s**; nothing came between 1 s and 5 min, and the 3 later ones were parked aircraft or recycled IDs (doc 12). So 300 s changes nothing in the
samples. Its downside: a pilot who lands, despawns and leaves would be marked dead (and the other side credited) if the parked aircraft is
destroyed within those minutes. **Recommendation:** keep 5 s, and revisit if a laggy server shows kill lines arriving later. A test pins the
behaviour at both 5 s and 300 s, so changing it is a one-line config change (`[replay] post_end_destroy_window_s`).

**OQ-31 Gunners: how should a player gunner get credit?**
The log credits gunner fire to the **aircraft**: no line ever names a turret or gunner as the shooter (doc 12). So today the pilot gets every
kill the gunner makes, and the gunner gets none. Options: (a) leave it (gunner sorties show deaths and time only); (b) a gunner gets an
**assist for every kill its aircraft makes while the gunner is aboard** (you can't tell who fired); (c) decide later together with gunner
stats (FR-WEB-14). **Recommendation:** (c), and record (b) as the likely rule.

**OQ-32 Do crashes before takeoff count as deaths and planes lost?**
In the end-to-end run, 777 of 5,845 lost aircraft (13%) never took off: taxi and takeoff crashes, mostly destroyed by the environment within
~4 minutes of spawning (a few were a replay bug, being fixed). Today they count in `deaths` and `planes_lost` (outcome `crashed`), which lowers
K/D and K/L for clumsy taxiing. Options: (a) count them (an aircraft is gone either way); (b) count them only when an attacker was involved
(airfield strafing); (c) never count them. **Recommendation:** (b): a strafed parked aircraft is a real loss, a taxi accident is noise.

## Score and ratings (later; shapes nothing in iteration 1)

**OQ-27 What makes a sortie "air superiority" or "attack"?**
Elo should only cover fighter-vs-fighter combat, and ground proficiency only attack sorties. But the F-51D, F-80C, F-84E (and sometimes the
MiG-15bis and F-86A-5) fly both roles. **Proposal:** decide **per sortie by loadout**: a sortie carrying bombs, rockets or napalm is an attack
sortie; guns only (drop tanks allowed) is an air-superiority sortie. The IL-10 is always attack. The catalog gets a `prop` / `jet` attribute
per aircraft. Alternative: a fixed role per aircraft type.

**OQ-28 Air-to-air Elo details** (FR-WEB-19)
Proposal to confirm or change: everyone starts at 1500. Only PvP kills between two air-superiority sorties update ratings (assists don't).
Each kill is one game won by the killer. Prop and jet ratings are separate pools. Cross-pool kills: a **jet killing a prop** changes nothing,
and a **prop killing a jet** updates both sides with a large weight (e.g. 2×). Deaths to AI, AA or self don't count. Stretch: per-aircraft
ratings built from the same games. Open: the K-factor (e.g. 32, lower after N games?), the cross-pool weight, and whether friendly kills
cost rating.

**OQ-29 "Time on target" for ground proficiency** (FR-WEB-20)
From the sample research (doc 12 has the data): transit takes ~10 min each way, the attack itself a median 2.5 min of a 22 min flight.
**Proposal:** a sortie's attack acts are hits and damage on ground targets, rocket salvos, bomb or napalm releases followed by an effect, and gun
bursts within 10 s of a ground hit. Acts less than 3 min apart form one pass; each pass is padded by 30 s on both sides; time on target =
the sum, at least 1 min. Ground score per hour = ground score / time on target. Pitfalls: no flight track (positions only exist at those acts),
releases that hit nothing can't be told from dropped tanks (21% of releasing sorties show no ground effect), and resupplied sorties have
several target visits.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Payload 59 for the F-51D is missing from the payload file (0.7%
of spawns don't resolve). Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
