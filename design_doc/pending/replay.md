# Pending decisions: core.replay

Decisions made while implementing `core.replay` that the design docs didn't specify. For the lead to merge into
`11_open_questions.md` / `02_functional_requirements.md` / `05_technical_decisions.md`.

## Structure

- **`feed()` only records facts; every rule runs at resolve time** (`snapshot()` = provisional, `finish()` = final, same code, so streaming and
  batch are identical and lookahead rules just see the whole history). Where: `state.py` (record), `resolve.py` (assemble), `judge.py` (one
  `Verdict` per sortie), `fate.py` (one function per rule, TD-16), `credit.py` / `kills.py` (kill and assist credit), `breakdown.py`
  (exchanges, hits per ammo, timeline). A snapshot is O(all recorded damage lines): meant for seconds-scale polling, not per event.
- **Object identity is the Python object, not the ID.** AType 12 for a known ID updates it (re-link, pilot `PID:-1` keeps its parent). A *different* type
  on a known ID creates a new object, unless the old one belongs to a still-open sortie (then it's an update). **The game recycles IDs a lot**:
  in 7 sample missions, 15,429 re-declarations carried another type than the object they re-used (47,991 carried the same), mostly statics and
  vehicles. So a same-type re-declaration of an object that was already destroyed (AType 3) and isn't a sortie aircraft is a new object too, and a
  new sortie never re-uses a destroyed or differently-typed object. (Without this, ground kills of re-spawned objects were swallowed.) Undeclared IDs seen in other
  events get a placeholder object (type ""), like il2_stats did. Where: `Replay._on_object_spawn`, `_get`.
- **Static block suffix `[g,i]` is stripped from the type** before lookup and in `object_types_seen` (`model.normalize_type`).
- **Crew bots are not in `object_types_seen` / `unknown_object_types`** (they're `cls=unknown, is_known=True` in the catalog anyway).
- **Zero-damage AType 2 lines are ignored** (il2_stats did). **Damage after an object's AType 3 is ignored** (il2_stats did). Explosion hit lines
  are dropped at `feed()` (never counted, TD-08). Hit lines are kept in memory per target for rule 5 and the per-ammo counts.

## Sortie scope and mission end

- **Aircraft destruction belongs to the sortie** if its AType 3 comes before the sortie end, within `post_end_destroy_window_s` = 5 s after it
  (new config; the shot-down shape logs AType 4 before AType 3. Measured on 14 missions, for player aircraft with a normal AType 4: 236 kills
  before it, 127 within 1 s after it, none from 1 to 60 s, 3 later), or at any
  time if the pilot left an airborne aircraft (AType 4 `PLID:0`, no AType 4, or a disconnect: the FR-ING-22 abandoned aircraft). A pilot kill
  counts within the same 5 s window. Where: `fate.aircraft_loss`, `pilot_death_tick`.
- **`mission_ended`** needs a *normal* AType 4 (`PLID` != 0) between the first AType 7 and +5 s (`mission_end_sortie_window_s`). A `PLID:0` end or a
  removal without AType 4 near mission end is a real pilot exit or a disconnect. Destruction or pilot death at/after the first AType 7 is
  despawn cleanup (the server destroys everything), not combat, and is ignored for such a sortie. A sortie that never took off keeps
  `not_taken_off`. A landed player sitting at mission end is `mission_ended` too (follows doc 12: "10% of sorties"). Open sorties at `finish()`:
  `mission_ended` if AType 7 was seen, else `in_flight`/`landed` with `pilot_fate = unknown`. Where: `fate.forced_by_mission_end`.

## Fate, outcome, status

- **Fate ladder** (`fate.pilot_fate_of`): gunner with AType 18 -> `bailed_out`/`event`; pilot bot killed -> `in_aircraft`; forced by mission end ->
  `mission_ended`; AType 4 `PLID:0` -> bailout rule v2 (`bailed_out`/`inferred`), else `exited_on_ground`/`inferred` (`unknown` when the pilot's
  final position is missing); no AType 4 (or AType 21 within 30 s while the aircraft was airborne at a plain end) -> `disconnected`, unless an attacker
  destroyed the aircraft (then `in_aircraft`); plain AType 4 -> `in_aircraft`/`event`. `disconnected` bool on the sortie = AType 21 within +-30 s of the
  end (or removal without AType 4): a landed player who leaves after despawning has `disconnected = True` but fate `in_aircraft`.
- **Bailout rule v2**: "last known aircraft position" = the AType 3 position if destroyed, else the latest position recorded for the aircraft
  (spawn, damage, kill, wheels, takeoff, landing, re-declaration). Rule 5 counts hits and damage on aircraft *and pilot bot* from any non-self attacker;
  "self" = the AID:-1 environment or any object of the sortie. Rule 7 uses `end >= first AType 7 - 60 s`. Where: `fate.bailout_v2`,
  `suspected_early_bailout`.
- **Disconnect (FR-ING-21)**: a disconnect without damage in the 120 s before it is neither death nor loss, and a later destruction of the abandoned
  aircraft is ignored (`loss = None`). With damage: death (`pilot_status = dead`), plane lost, `loss_cause` by attacker involvement. Where:
  `judge.judge`.
- **Pilot death** = pilot bot AType 3 in scope, or fate `in_aircraft` with the aircraft lost (the pilot went down with it). `is_plane_lost` = aircraft
  destroyed in scope, pilot killed, bailed out, or disconnect death.
- **Outcome ladder** (`judge._outcome`): any loss/death/bailout -> `shot_down` if `loss_cause = attacker` else `crashed` (a bailout with no AType 3 is
  `crashed`/`shot_down` too, as in il2_stats); not taken off; `mission_ended`; open sortie -> `in_flight`/`landed`; disconnect without damage ->
  `unknown` if airborne else landed/ditched; airborne at a normal end (despawned in the air) -> `in_flight`; on the ground -> `landed` if the last
  landing was within 4 km of a friendly airfield (`areas.AIRFIELD_RADIUS_M`, il2_stats), `ditched` otherwise. No friendly airfield logged -> `landed`
  (can't tell).
- **Capture** (`is_captured`, `pilot_status = captured`): an alive pilot whose bailout position, ground-exit position or last landing position lies in
  an enabled influence area (AType 13/14, 2D polygons) of another non-neutral coalition. Not set for mission-ended sorties.
- **`loss_cause`**: `attacker` if the kill line names an attacker that isn't the sortie itself, or any attacker hit/damage on aircraft or pilot
  happened before the loss; `self` otherwise; `none` when nothing was lost. **Structural failure** uses the first AID:-1 damage line on the aircraft
  (so no self-damage line = not flagged) and the first AType 31/6 after the AType 3 (none = not flagged, as documented).
- `damage_taken` = sum of the aircraft's own damage lines up to the loss tick (or sortie end), clamped to 1. Aircraft status `destroyed` only when
  an AType 3 was in scope.

## Kills and credit

- **Credit is resolved for every lost player aircraft, not only bailouts/disconnects**: explicit AID wins (unless it is the sortie itself); with
  AID:-1 the attacker with most damage wins; every other damager with >= `assist_min_damage` (1%, new config; il2_stats used > 1%, and gave an
  assist to the second damager only) gets an assist. Damage to the pilot bot and turrets counts as damage to the aircraft (il2_stats did). So an
  aircraft shot up and later crashing on its own is credited to the shooter, consistent with `loss_cause = attacker`. `via` = `direct` (explicit killer
  or no pilot exit), `abandoned_aircraft` (bailout / ground exit), `disconnect`. `credit = "shared"` is never emitted. Where: `credit.credit_kill`,
  `kills.resolve_kills`.
- **Disconnect or bailout without an AType 3** for the aircraft still creates the victim record (tick = sortie end), so damage-based credit applies
  (FR-ING-21/22 say "credit if an attacker caused the recent damage"; credit uses all attacker damage in the sortie, not only the last 2 minutes).
- **Victims**: every destroyed object that isn't a crew bot, a gunner turret or `ordnance` class, **including static objects** (fences, storage
  stacks: 52k "ground kills" over the samples vs 2.4k air). `KillResult.victim_type` lets ingest filter by class later; sortie counters
  `kills_ground` currently include them. Decide in the aggregation layer whether fences should score.
- **Gunners**: a player gunner's kills go to the gunner's own sortie only (not copied to the pilot); an AI turret on a player's aircraft credits the
  pilot. A gunner is never a kill victim. Gunner flight state comes from the parent aircraft.
- **Friendly fire**: killer and victim coalition equal and non-zero -> `is_friendly`; not counted in `kills_*`/`assists`; timeline `friendly_fire`.
- A player victim killed by the environment with nobody to credit still gets a `KillResult` (killer `None`) so the death is on record.

## Breakdowns

- Damage exchanges and hits are grouped per counterpart (`object_type`, `sortie_index`), by the aircraft root (so crew and turrets fold into the
  aircraft). Self damage is left out. Hits per ammo type count every non-explosion hit line the sortie gave or received. Damage-to-ammo attribution
  (FR-WEB-18) and ordnance counting are **not** implemented (iteration 1.x).
- Timeline kinds: `spawn`, `takeoff`, `landing`, `kill`, `assist`, `friendly_fire`, `shot_down`/`destroyed`, `bailout`, `disconnect`, `sortie_end`.
- `takeoff_tick` for an air start = the spawn tick (`takeoffs` counts AType 5 only). `flight_time_s` = sum of airborne intervals (AType 5 to 6 or to the
  end). Both stop at the aircraft's loss tick when it was destroyed first: logs write AType 6 for the falling wreck, which isn't a landing.

## Config and contract changes

- `ReplayRules`: added `post_end_destroy_window_s = 5.0` and `assist_min_damage = 0.01` (defaults, additive). `result.py` and `events.py` unchanged.
