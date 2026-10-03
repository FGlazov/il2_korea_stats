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

**OQ-36 A disconnect long after the aircraft was destroyed: a loss?** (Claude's default applied unless you object)
10 sample sorties end with the player's removal (no AType 4) 131–683 s after their aircraft was destroyed in the air, mostly by the
environment: the pilot crashed, stayed in the game for minutes, then left. FR-ING-21 only counts a disconnect as a death with damage in the
120 s before it, so today these show "no loss" although the aircraft was destroyed. **Default (gut call):** a destruction that happens
**before** the disconnect is a normal loss of that sortie (crashed / shot down by cause, and a death unless the pilot is known to have
left the aircraft); the 120 s rule only decides about aircraft abandoned *by* the disconnect. Small change, to be implemented with the next
replay batch.

**OQ-37 Ship Tabler Icons (MIT) as the placeholder icon set now?** (Claude's default applied unless you object)
The icon research (doc 16) found Tabler Icons covers almost every slot in doc 15 under the same licence as the project (only a copyright
line in NOTICE). **Default:** replace the hand-drawn placeholders with Tabler icons now (aircraft silhouettes, artillery, AA, logo and the
header texture stay our own drawings), so the site looks finished until the designer delivers.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
