# 13 — Game Rules (replay)

How `core.replay` turns a mission's events into sorties, fates, outcomes, kills and breakdowns. This is the **rulebook**: every rule here
has a scenario test (TD-21). The requirements behind them are in [02](02_functional_requirements.md) (FR-ING-14, 17, 21, 22, 23, 24); the
log facts they rely on are in [12](12_korea_log_format.md). Status `[DECIDED]` (maintainer review, 2026-10-03) unless tagged otherwise.
Thresholds are `ReplayRules` fields (`core/replay/config.py`), settable in `[replay]` of `il2ks.toml`.

## Structure

- **`feed()` only records facts; every rule runs at resolve time.** `snapshot()` (provisional) and `finish()` (final) run the same code, so
  streaming and batch give identical results and lookahead rules simply see the whole history (TD-07). For live sorties (FR-ING-15) the
  running mission is resolved every few minutes and each pass replaces the previous one; pages say the result may still change.
- Code: `state.py` records facts, `resolve.py` assembles the result, `judge.py` gives one verdict per sortie, `fate.py` holds one function per
  rule (TD-16), `credit.py` / `kills.py` do kill credit, `breakdown.py` builds exchanges, hits and the timeline.

## Objects

- **Object identity is the tracked object, not the log ID.** The game recycles IDs a lot (in 7 missions, 15k re-declarations carried a different
  type than the object they reused). AType 12 for a known ID **updates** it (re-link; a pilot with `PID:-1` keeps its parent). It creates a
  **new** object when the type differs, or when the old object was already destroyed and isn't a sortie aircraft. A new sortie never reuses a
  destroyed or differently typed object. IDs seen before any AType 12 get a placeholder object (as il2_stats did).
- **Recycled IDs after a sortie end** (2026-10-03): an AType 12 on an ended sortie's aircraft or bot ID more than
  `post_end_destroy_window_ground_s` (5 s) after the end is a **new object**, even with the same type. The shot-down shape (AType 4, a same-type
  re-declaration within ~0.3 s, then AType 3) stays an update. Without this, a reused ID destroyed minutes later counted as the old sortie's
  loss (in the samples bot IDs were re-declared 507 times after a sortie end, 15 of them then destroyed within 300 s), and a gunner whose turret
  ID was reused got linked to another aircraft.
- Static block suffixes `[g,i]` are stripped from types. Crew bots aren't reported as seen or unknown types.
- **Parked reset** `[PROPOSED]` (2026-10-03): sometimes a player's parked aircraft gets an environment AType 3 (with a same-tick re-declaration)
  and then takes off and flies normally, so the game evidently restored it (7 aircraft in 210 missions). A takeoff (AType 5) of an open
  sortie's own aircraft after such a destruction **undoes it**, together with that tick's environment damage.
- Ignored as in il2_stats: zero-damage AType 2 lines, and damage after an object's AType 3. Explosion hit lines are never counted as hits
  (TD-08); they're kept in memory only to label ordnance (FR-WEB-18, it1.x).

## Sortie scope and mission end

- A sortie runs from AType 10 (spawn) to its AType 4 (end), or to the pilot's removal when there is no AType 4 (a disconnect).
- **The aircraft's destruction counts for the sortie** when its AType 3 comes before the sortie end, or within the post-end window after it,
  or at any time if the pilot left an airborne aircraft (bailout, exit or disconnect: the abandoned aircraft of FR-ING-22). The window exists
  because a shot-down sortie logs AType 4 *before* AType 3. The same window applies to the pilot's own death.
  - **300 s** (`post_end_destroy_window_s`) for a pilot sortie whose aircraft was **airborne** at the sortie end: the maintainer chose 5 minutes
    to be safe against server lag (OQ-30, 2026-10-03).
  - **5 s** (`post_end_destroy_window_ground_s`) for everything else: aircraft on the ground at the end (landed or never took off), and
    **gunner** sorties. This guard is `[PROPOSED]` (Claude): it keeps a pilot who landed, despawned and left from being marked dead when the
    parked aircraft is destroyed minutes later, and keeps a gunner who left a flying aircraft from dying with it later.
  - Measured on 210 missions: every post-end kill line came within 1 s, and with these rules **no pilot sortie changes** against a 5 s window.
    A plain 300 s for everyone would have changed 2 gunner sorties (one of them killed by the server's cleanup after AType 7), hence the
    gunner rule.
- **Mission end.** The server force-ends every running sortie right after AType 7. A sortie is **forced by mission end** when it has a
  *normal* AType 4 (the pilot was still in the aircraft, `PLID` ≠ 0) between the first AType 7 and 5 s after it
  (`mission_end_sortie_window_s`). A `PLID:0` end, or a removal without AType 4, near mission end is a real exit or disconnect, not forced.
  For a forced sortie, destruction or death at or after AType 7 is the server's despawn cleanup, not combat, and is ignored.
  A landed player sitting on the ground at mission end is forced too. Sorties still open at `finish()`: forced if AType 7 was seen, else
  left open (`in_flight` / `landed`, fate `unknown`).

## Fate and outcome

Two separate answers per sortie:
- **Pilot fate** = *where the pilot ended up*: still in the aircraft, bailed out, climbed out on the ground, disconnected, or cut off by mission
  end. It comes from how the sortie ended (AType 4 `PLID`, the pilot's last position, AType 21, AType 18, mission end, the pilot bot's own AType 3).
- **Outcome** = *what happened to the sortie and its aircraft*: landed, ditched, crashed, shot down, still flying, never took off, mission ended.
  It comes from whether the aircraft was lost, who caused it, takeoff and landing.

They're independent: a `disconnected` pilot can have outcome `shot_down`; a `bailed_out` pilot can have outcome `crashed` (an undamaged bailout).
Death, aircraft loss and capture are separate flags (`is_death`, `is_plane_lost`, `is_captured`), derived once here so level 2 only sums them.

### Pilot fate (`fate.pilot_fate_of`)
Checked top to bottom, the first match wins. Result is `fate / source`.

```
Is it a gunner with an AType 18 (bailout event)?
├─ yes → bailed_out / event
└─ no: Was the pilot bot killed (its own AType 3, in scope)?
   ├─ yes → in_aircraft / event                      (killed in the aircraft, even if the player then disconnected)
   └─ no: Was the sortie forced by mission end?
      ├─ yes, with an AType 4 → mission_ended / event
      ├─ yes, still open      → mission_ended / inferred
      └─ no: How did the sortie end?
         ├─ AType 4 PLID:0 (pilot not in the aircraft)
         │  ├─ bailout rule v2 holds (FR-ING-14) → bailed_out / inferred
         │  ├─ pilot's final position known      → exited_on_ground / inferred
         │  └─ position missing                  → unknown / unknown
         ├─ disconnect: no AType 4 (pilot just removed),
         │  or a normal AType 4 with an AType 21 within ±30 s while airborne
         │                                       → disconnected / inferred   (also when an attacker destroyed the aircraft)
         ├─ normal AType 4 (PLID = the aircraft) → in_aircraft / event
         └─ still open                           → unknown / unknown   (snapshot: in_aircraft / inferred)
```

**Bailout rule v2** (FR-ING-14; re-checked 2026-10-03, doc 12 "Order of AType 4 and AType 16"): the **airborne gate decides almost
everything**; among 1,378 airborne `PLID:0` candidates the 100 m distance test only removes 9 (crash landings and mountain impacts), and any
threshold from 25 to 100 m gives the same result ±9, so 100 m stays. Without the airborne gate, 373 ground exits would look like bailouts
(the aircraft's last known position is stale after taxiing). A pilot position outside the map bounds (|x|, |z| > 1e6, y outside −1,000…20,000)
counts as missing. In a live snapshot, a `PLID:0` end whose AType 16 hasn't arrived yet is pending, not `unknown`; at `finish()` a missing
AType 16 falls back to the pilot's latest re-declaration position.
Rule: `PLID:0`, and the aircraft was airborne (at its destruction, else at sortie end), and the pilot's final position
is ≥ 100 m from the aircraft's last known position, and the pilot didn't die within 0.5 s of the aircraft. "Last known position" = the AType 3
position if destroyed, else the latest position recorded for the aircraft (spawn, damage, kill, wheels, takeoff, landing, re-declaration).
**Suspected early bailout** adds: no hits or damage on aircraft or pilot from any attacker (environment and the sortie's own objects don't
count), no disconnect, and the sortie didn't end within 60 s before the first AType 7.

`disconnected` (the flag, not the fate) = an AType 21 within ±30 s of the sortie end, or removal without AType 4. A player who lands, despawns
and then leaves has `disconnected = true` but fate `in_aircraft`.

### Loss, death and cause (`judge.judge`)

```
loss          = the aircraft's AType 3, if in scope (see Sortie scope); dropped if forced and at/after AType 7
died          = the pilot bot's AType 3 in scope (gunner: or its turret's, unless it bailed out); same mission-end rule
in the aircraft when it was lost?  fate in_aircraft, or fate disconnected with an attacker's kill line
              → the pilot died with it: died = loss tick
disconnect death (FR-ING-21) = fate disconnected, no attacker kill line, and ANY damage (any source) on aircraft or crew
              in the 120 s before the disconnect
fate disconnected without that damage and without an attacker kill → no loss, no death (the abandoned aircraft's later crash is ignored)

is_plane_lost = loss, or died, or bailed out, or disconnect death
loss_cause    = none      if nothing was lost
              = attacker  if the kill line names an attacker other than the sortie itself,
                          or any attacker hit or damage on the aircraft or crew came before the loss
              = self      otherwise (terrain, overstress, collision with nobody to blame, abandoned aircraft)
is_death      = died or disconnect death
is_captured   = alive, not forced, and the pilot's bailout / ground-exit / last landing position lies in an enabled influence area
                (AType 13/14, 2D polygons) of another non-neutral coalition
pilot_status  = dead > captured > wounded (pilot bot took any damage) > healthy
aircraft_status = destroyed (loss) > damaged (damage_taken > 0) > unharmed;  damage_taken = own damage lines up to the loss, capped at 1
```

A **bailout without any AType 3** for the aircraft still counts as a loss (the aircraft was abandoned in the air), as in il2_stats. So a missing
destruction line can't hide a bailout. A missing *bailout* signal (no `PLID:0`) means the pilot is read as having stayed in the aircraft, and if
that aircraft was destroyed, as having died with it. That's the conservative reading.

A pilot who crashes into a mountain with no attacker involvement is `crashed`, `loss_cause = self`, and nobody gets credit; the death is still
recorded (a kill entry with no killer).

**Losses on the ground** (`fate.ground_loss`, OQ-32, 2026-10-03). A crash before takeoff **counts as a death and an aircraft lost like any
other** (option a: "we want to encourage players to taxi well"). Two extra flags feed a "hall of shame" on the profile:
```
taxi_accident     = plane lost, loss_cause self, and no takeoff (AType 5) and no air start before the loss
strafed_on_ground = plane lost, loss_cause attacker, the aircraft on the ground at the loss (never took off, or landed and not
                    taken off since), and every attacker hit or damage came after that landing
                    (shot up, crash-landed, then destroyed = shot down, not strafed)
both false for gunners; derived from the final is_plane_lost / loss_cause, so a disconnect death on the ground can be a taxi accident
```
Samples (15,245 pilot sorties): **767 taxi accidents** (median 207 s after spawn, ~1 km from the spawn point: taxi and takeoff-run crashes,
not parked aircraft) and **19 strafed** (9 parked, 10 after landing). The timeline's `destroyed` / `shot_down` entry says which.

**Structural failure** (FR-ING-17 v2) uses the first `AID:-1` damage line on the aircraft and the first wheels-on / landing after the AType 3
(none = not flagged).

### Outcome (`judge._outcome`)

```
Was the aircraft lost (is_plane_lost)?
├─ yes → shot_down if loss_cause = attacker, else crashed
└─ no: Did it ever take off?
   ├─ no  → not_taken_off
   └─ yes: Forced by mission end?
      ├─ yes → mission_ended
      └─ no: Is the sortie still open?
         ├─ yes → in_flight if airborne, else landed
         └─ no: Did the pilot disconnect (fate disconnected, no death)?
            ├─ yes → unknown if airborne at the end, else landed / ditched (below)
            └─ no: Airborne at the end (despawned in the air, or a gunner who left a flying aircraft)?
               ├─ yes → unknown          (in_flight is only for sorties still open in a snapshot)
               └─ no  → landed / ditched:
                        landed  if the last landing was within 4 km of a friendly airfield,
                                or no landing / no friendly airfield was logged (can't tell)
                        ditched otherwise
```

## Kills and credit

- **Credit is resolved for every lost player aircraft**, not only after bailouts and disconnects (so an aircraft shot up that later crashes on
  its own is credited to the shooter, consistent with `loss_cause = attacker`): an explicit `AID` on the kill line wins unless it's the sortie
  itself; with `AID:-1` the attacker with the **most damage** wins. Every other damager with ≥ 1% damage (`assist_min_damage`) gets an
  assist (il2_stats gave only the second damager an assist). Damage to the pilot bot and turrets counts as damage to the aircraft. `via` =
  `direct`, `abandoned_aircraft` (bailout or ground exit) or `disconnect`. There is no shared credit.
- A disconnect or bailout without an AType 3 for the aircraft still creates the victim record (at sortie end), so damage-based credit applies.
- **Victims**: every destroyed object except crew, equipment (parachutes, ejection seats, spotters, vehicle turrets), gunner turrets and
  ordnance. **Static objects count as
  ground kills** (maintainer: keep everything, score them low later).
- **Coverage**: a kill record exists for every kill with a player on at least one side, PvE included (FR-WEB-21). AI-vs-AI is dropped. Only
  PvP becomes a `Kill` row; the rest feeds sortie counters and timelines.
- **Gunners** `[PROPOSED]` (2026-10-03):
  - **The log credits gunner fire to the parent aircraft** (doc 12): no line ever names a turret or gunner as attacker. So in practice the
    **pilot gets every kill their gunner makes**, and a gunner sortie gets none. The code also has the opposite rule ready (a gunner's own kill
    gives the player pilot an assist) in case a game update starts naming turrets; it never fires on current logs. How to reward gunners: OQ-31.
  - A player gunner can **die**: the gunner sortie records the death and its killer (when the aircraft is shot down, or the gunner bot is
    killed). It is **not** a separate kill for the attacker, who already gets the aircraft. A gunner's flight state comes from the parent
    aircraft.
- **Friendly fire** (FR-ING-23): killer and victim in the same non-zero coalition → `is_friendly`. Not counted as kills or assists; counted as
  friendly kills, plus friendly hits and damage per sortie; timeline entry `friendly_fire`. Friendly kills include own-side AI and static
  objects (a friendly fence counts), so expect them to look high until the breakdown by class (FR-WEB-21) is on the page. Friendly hits and
  damage stop at the shooter's sortie end and exclude the sortie's own aircraft and crew.

## Breakdowns and timeline

- Damage exchanges and hits are grouped **per counterpart** (object type + sortie, if it's a player), folded to the aircraft (crew and turrets
  count as their aircraft). Self damage is left out. Hits per ammo type count every non-explosion hit line given or received.
- **Timeline** = the sortie page's event list. Entry kinds: `spawn`, `takeoff`, `landing`, `kill`, `assist`, `friendly_fire`,
  `shot_down` / `destroyed` (the aircraft), `killed` / `died` (the pilot or gunner died while the aircraft survived), `bailout`, `disconnect`,
  `sortie_end`; each with time, position and counterpart. The killer is named for gunners too.
- "Took off", takeoffs and flight time all stop at the aircraft's loss, so they can't disagree.
- `takeoff` time of an air start = the spawn time (`takeoffs` counts real AType 5 only). `flight_time_s` = sum of airborne intervals. Both stop at
  the aircraft's loss: logs write a "landing" for a falling wreck.

## Ammo and resupply

- v1 stores per ammo type: loaded (AType 10), left (AType 4), used (loaded − left, or unknown, see below) and hits. The damage-per-ammo attribution of FR-WEB-18 is it1.x.
- **Resupply** (FR-ING-24): a player can land, rearm and take off again in one sortie, so "loaded − left" undercounts what was fired, and
  AType 24 gun bursts carry no round count. The log has **no resupply event** (doc 12), so it's inferred `[PROPOSED]`: with
  `replay.resupply_allowed = true` (default; most servers allow it), a landing followed by another takeoff in the same sortie marks the sortie
  `resupplied`, and "ammo used" is unknown for it (hits, releases and rocket salvos are still exact). With `false`, "loaded − left" is used.
  "Used" is also unknown for bombs when "left" exceeds "loaded" (IL-10 bomblet payloads count stations when loaded and bomblets when left).
  About 2% of sorties take off more than once.

## Combat role, time on target and ratings

Inputs for the later air and ground scores (FR-WEB-7, 19, 20), computed at ingest from iteration 1 so the pages need no reprocess. Decided by the
maintainer on 2026-10-03 (OQ-27, 28, 29); thresholds are config values and first guesses.

**Combat role** (`attack.combat_role`), pilot sorties only (gunners: none):
- `attack` if the aircraft spawned with bombs or rockets (AType 10 ammo counts; napalm tanks count as bombs, Tiny Tims as rockets), or its
  catalog class is `attacker` (IL-10). Otherwise `air_superiority`; drop tanks carry no ammo count, so a guns + tanks loadout stays air
  superiority. A sortie that never took off still has a role.
- The ammo counts are used, not the payload name: they exist for every spawn (also a payload missing from the CSV) and agree with the payload
  names on every sample spawn.
- Samples: F-51D 70% attack, F-80C 87%, F-84E 94%, IL-10 100%, F-86A-5 36%, MiG-15bis 19%, La-11 and Yak-9P 0%.

**Time on target** (`attack.time_on_target_s`), attack sorties only (others: none; no qualifying release: 0):
```
release      = AType 25 (store) or AType 26 (rocket salvo) by the sortie's aircraft, from spawn to the sortie end or the aircraft's loss
qualifying   = an enemy ground object lies within tot_target_radius_m (3 km), horizontally (x, z; altitude y ignored)
               enemy:  coalition non-zero and not the sortie's
               ground: class tank, vehicle, aaa, ship or static
               position: its last logged one at or before the release (AType 12 spawn, AType 2/3 lines naming it as the target)
               it must exist and not be destroyed yet at the release
attacks      = qualifying releases sorted by time; a gap > tot_pass_gap_s (300 s) starts a new attack
one attack   = from (first release − tot_lead_in_s (60 s)) to its last release; the run-in never starts before the takeoff of that
               flight (a resupplied sortie's later takeoff counts) or before the previous attack's last release
time on target = sum over attacks
```
- A release far from every enemy ground object (a jettison when intercepted) counts for nothing, as the maintainer asked. A single qualifying
  release is worth the 60 s run-in.
- Known noise `[PROPOSED]` (accepted for now): the log can't tell a drop tank from a bomb, so a drop tank released near an enemy object counts;
  the release position is the carrier's, not the impact point (a jet releasing high can hit 1–2 km ahead, so 3 km is generous for jets);
  vehicles that moved without being hit are taken at their last logged position.
- Samples, attack sorties that took off: 37.5% have 0 (shot down or turned back before attacking, or released nowhere near a target); median
  60 s, top 10% ≥ 250 s, max 18 min; about 10% of the flight time where nonzero. 64% of releases qualify. Cost: about 2 ms per mission.

**Air-to-air Elo** (`core/ratings/elo.py`, computed in `ingest/ratings.py` across all missions, doc 14):
- A **game** = one PvP kill credit (not an assist, not friendly) where killer and victim are both pilot sorties with role `air_superiority`.
  Kills by AI, AA or the environment have no `Kill` row and never count. A player killing their own other sortie is ignored.
- Each player has a **prop** and a **jet** rating (pool = the propulsion of the aircraft flown in that sortie), starting at `start` (1500).
- Same pool: standard Elo. Expected E = 1 / (1 + 10^((R_loser − R_winner) / 400)); the winner gains K × (1 − E) and the loser loses the same
  (K = `k`, 32).
- **Jet kills prop**: no change and no game counted ("not impressive").
- **Prop kills jet**: the killer's prop rating and the victim's jet rating move by K × `cross_pool_weight` (2.0) × (1 − E), E from those two
  ratings.
- Games are replayed in time order (mission start, then kill time), so the result doesn't depend on import order.
- Each pool alone isn't zero-sum: a prop-kills-jet game moves points from the jet pool into the prop pool (in the samples 36 such games
  moved ~1,050 points; prop players average +7, jet players −3.5). Intended: it's the reward for the impressive kill.
- Samples: 807 games (164 prop-prop, 528 jet-jet, 36 prop-kills-jet, 79 jet-kills-prop that change nothing) out of 2,327 kill credits
  (attack sorties don't play). 150 prop and 298 jet players rated; 5 and 26 with ≥ 10 games. Ratings span 1,377–1,806; the biggest single
  change is 37 points.
- Stretch (not built): per-aircraft-type ratings from the same games.
