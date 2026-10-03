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

**OQ-33 How do pages show ground kills of static objects?**
Statics count as ground kills (decided 2026-10-03, scored low later). On the samples that dominates the numbers: the top player has
6,990 ground kills against 64 air kills, five sorties have 300+ (max 519), and one F-80C pass got 16 kills from fences and yard boxes. A
profile showing "Ground kills: 6,990" invites ridicule. Options: (a) show the total as is; (b) add a `kills_static` counter (a subset of
ground kills, cheap at ingest) and show "Ground kills 6,990 (5,800 static objects)" or split into two columns; (c) show only non-static
ground kills on the pages until scoring weights them. **Recommendation:** (b), it keeps the decision and makes the number honest.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Payload 59 for the F-51D is missing from the payload file (0.7%
of spawns don't resolve). **Also check F-51D payloads 57–59**: the logged ammo says 57 = 2 bombs + 4 rockets, 58 = 2 bombs + 6 rockets, 59 =
4 rockets only, but the CSV names 57 "napalm + 6 ATAR" and 58 "75 gal drop tanks + 4 ATAR", so the rows look shifted by one (likely 57 =
napalm + 4 ATAR, 58 = napalm + 6 ATAR, 59 = drop tanks + 4 ATAR). Only displayed names are affected; no rule reads them. Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
