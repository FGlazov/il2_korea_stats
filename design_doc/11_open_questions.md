# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected.

## Lower impact

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps**
Is there a source for `WM` weapon-modification names, like the payload file? Payload 59 for the F-51D is missing from the payload file.
(Redistribution is settled: the payload file ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.

## Iteration 1 implementation decisions to review (2026-10-03)

Decisions made while writing the first ingest prototype that the design docs don't cover. Each one is in the code already;
confirm, change, or reject. IDs `OQ-I1-*` are for this batch only.

**OQ-I1-1 Contracts between layers.** Events are frozen dataclasses in `core/logparse/events.py` (one per AType, unmapped keys in
`extra`, unused ATypes as `GenericEvent`). The replay output is `core/replay/result.py` (`MissionResult` with `MissionInfo`,
`SortieResult`, `KillResult`), with all times in ticks; `ingest` turns ticks into UTC datetimes.

**OQ-I1-2 `KillResult` covers every kill with a player on at least one side**, not only PvP. `ingest` stores only PvP rows in `Kill`
(doc 06) and uses the rest for sortie counters and timelines.

**OQ-I1-3 Extra sortie columns not in doc 06**: `is_death`, `is_plane_lost`, `is_captured` (so level 2 sums booleans instead of re-deriving
rules from outcome and pilot fate), `takeoffs`, `landings`, `payload_name`, spawn position columns, and `account_uuid` + `spawn_tick` on
`PlayerSortie` (the natural key). Outcome adds a `mission_ended` value next to the pilot fate of the same name.

**OQ-I1-4 Counters shared by `PlayerMission`, `Player` and `PlayerAircraft`**: sorties, flight time, air and ground kills, assists,
deaths, planes lost, bailouts, suspected early bailouts, captures, takeoffs, landings (doc 06 lists "..."). Mission keeps
`players_total`, `sorties_total`, `redfor_sorties`, `blufor_sorties`, `kills_air`, `kills_ground`.

**OQ-I1-5 `Kill` natural key** is `(killer_sortie, victim_sortie, tick, credit)`.

**OQ-I1-6 Ammo**: v1 stores loaded and left counts (AType 10 and 4) and hits per ammo type (no `explosion`). The FR-WEB-18 damage
attribution and ordnance labelling are left for iteration 1.x.
