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
  For a forced sortie, destruction or death at or after AType 7 is the server's despawn cleanup, not combat, and is ignored, and so is
  **damage** at or after AType 7 (`damage_taken`, wounded status, damage breakdown, hits received; 2026-10-03: the cleanup writes a 1.0
  environment damage line, which had shown 105 surviving aircraft as fully damaged).
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

**What pages show as the pilot's fate** (maintainer, 2026-10-04, display only; OQ-106): **Dead** if `is_death` (or status dead), else **Captured**
if `is_captured` (or status captured), else **Survived**, whatever the stored `pilot_fate` says (`unknown` reads Survived). The stored fate
(`bailed_out`, `exited_on_ground`, `disconnected`; nothing for `in_aircraft` and `unknown`) is only the detail: a tooltip on the lists, a note on
the sortie page. Nothing here changes how the replay decides (`web/display.py::pilot_fate_key`).

### Pilot fate (`fate.pilot_fate_of`)
Checked top to bottom, the first match wins. Result is `fate / source`.

```
Is it a gunner with an AType 18 (bailout event)?
├─ yes → bailed_out / event
└─ no: Was the pilot bot killed (its own AType 3, in scope)?
   ├─ yes → in_aircraft / event                      (killed in the aircraft, even if the player then disconnected)
   └─ no: Was the sortie forced by mission end?
      ├─ yes, with an AType 4 → in_aircraft / event     (+ ended_by_mission_end)
      ├─ yes, still open      → in_aircraft / inferred  (+ ended_by_mission_end)
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
counts as missing. In a live snapshot, a `PLID:0` end whose AType 16 hasn't arrived yet is pending (fate `in_aircraft / inferred`, like an open sortie, so no
transient false loss); at `finish()` a missing AType 16 falls back to the pilot's AType 12 re-declaration position within
`pilot_pos_fallback_window_s` (5 s) of the sortie end, else the final position is unknown.
Rule: `PLID:0`, and the aircraft was airborne (at its destruction, else at sortie end), and the pilot's final position
is ≥ 100 m from the aircraft's last known position, and the pilot didn't die within 0.5 s of the aircraft. "Last known position" = the AType 3
position if destroyed, else the latest position recorded for the aircraft (spawn, damage, kill, wheels, takeoff, landing, re-declaration).
**Bailout rule v3 candidates (Rufus, 2026-10-03)** `[DECIDED]` (to test against v2 and adopt what improves it; must ship with the first
public release, maintainer 2026-10-03). Rufus, who builds the other IL-2 Korea stats system, shared the two methods his system combines:
1. **Ejection spawn.** On an ejection the log writes an ordinary AType 12 spawn for the pilot body (`TYPE:BotPlanePilot_USAF1950jet` etc.,
   class `aircraft_pilot`) that **reuses the bot's own id** (the `PID:` of the player's AType 10 spawn line). The discriminator is the
   parent: a real ejection announces the bot **detached, `PID:-1`**. The logger also re-announces a still-seated bot whenever it needs to
   reference it (damage to the pilot, etc.); those carry `PID:<aircraft id>`. Gates: the aircraft must be **airborne** (took off, no landing
   since), because the same spawn fires for a pilot climbing out after landing, aborting before take-off or disconnecting on the ground;
   and a **bot id → sortie map from AType 10** is needed, because a jet's crew usually has no spawn line of its own before this moment.
   Detects only about a third of bailouts: not when the pilot was damaged, or the aircraft isn't already destroyed.
2. **Geometry at AType 16** (like v2). A bailout when all hold: the sortie's aircraft is **destroyed**; it **never landed** (a Landing
   strictly *after* the kill tick is the falling wreck hitting the ground and still counts as "never landed"; a crash-landing resolves its
   kill at the landing tick, so a pilot walking away from that wreck is not a bailout); **no disconnect** on the sortie (the AType 21 line
   precedes the teardown, so it's already known); the pilot **isn't already dead**; and either the teardown position is **> 200 m** from the
   aircraft's last logged position, **or > 30 m above ground** (heightmap at that spot). The second arm matters: the wreck's logged position
   freezes at the kill point while the real airframe glides on, so a pilot who bails and quickly clicks "end mission" can be torn down
   within 200 m of the "wreck" while still hanging under the canopy.
Rufus also finds the `AType 4 PLID:0` signal that v2 uses promising and plans to add it to his system. Differences from v2 to evaluate:
v2 doesn't require the aircraft to be destroyed (an undamaged bailout whose aircraft has no AType 3 yet), uses 100 m instead of 200 m, has
no height arm and no ejection-spawn signal. The height arm needs terrain height per map (**OQ-39**). Credit: Rufus (IL-2 Korea stats
developer), shared via the maintainer.

**Bailout rule v3 as built** `[PROPOSED]` (2026-10-03, branch to merge; evaluated with `il2ks dev bailout-eval` on 210 missions, 11,364
pilot sorties that took off). Bailout = `AType 4 PLID:0` and either:
1. **Ejection spawn** (Rufus's method 1): an AType 12 of the player's bot id (from AType 10) with `PID:-1`, between spawn and sortie end
   + 1 tick, the aircraft airborne at that tick by AType 5/6, and the pilot's AType 3 **not** within 0.5 s (`died_with_aircraft_s`) → fate
   `bailed_out / event`; it overrides v2's airborne-flag and distance tests. The gates matter: 49 of 378 raw `PID:-1` hits are pilots killed
   in the seat (announced detached at death), and forced mission-end cleanup also writes `PID:-1` (excluded by requiring `PLID:0`).
2. **Rule v2 unchanged**, plus the pilot is **not already dead** at the sortie end (Rufus's "pilot isn't already dead") → `bailed_out /
   inferred`.
Results: `bailed_out` 1359 → 1360 (326 `event`, 1034 `inferred`); suspected early bailouts 361 → 296 (66 pilots who died in the seat
no longer count). Ejection spawn finds ~1/3 of bailouts (327 of v2's, plus a real one v2 missed because of a stale wheels flag), as Rufus
said. **Rufus's method 2 is not adopted**: v2 finds all but 9 of its bailouts and those 9 are crash-landing ground exits (its AType 5/6
"airborne" is worse than the live wheels flag; "never landed" as worded drops ~17 real bailouts of aircraft that landed earlier and flew
again). The 200 m threshold would lose ~28 real high-altitude bailouts in the 100–200 m band, so 100 m stays. A terrain-free "above
ground" proxy (lowest known ground sample nearby) was too weak (2% false at 500 m but covering 57 of 1,359; 14% false at 2 km), so the
height arm waits for heightmaps (OQ-39). Left: a pilot shot under the canopy after ejecting keeps fate `in_aircraft` (2 cases).

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
destroyed BEFORE the disconnect (any time; OQ-36, maintainer 2026-10-03: "exit server" instead of "end sortie" is fine to press)
              → a normal loss of the sortie (outcome, cause and credit as usual) and a death if the pilot was still in the aircraft;
                the 120 s damage rule above only decides about aircraft that were intact when the player left

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

A pilot killed under the parachute (the pilot bot's AType 3 after a detected bailout) is **always a death** (maintainer, OQ-99, 2026-10-04); the
aircraft was already lost and credited, and the shooter keeps the kill. The old `[rules] parachute_deaths` toggle is gone.

A pilot who crashes into a mountain with no attacker involvement is `crashed`, `loss_cause = self`, and nobody gets credit; the death is still
recorded (a kill entry with no killer).

**Losses on the ground** (`fate.ground_loss`, OQ-32, 2026-10-03). A crash before takeoff **counts as a death and an aircraft lost like any
other** (option a: "we want to encourage players to taxi well"). Two extra flags feed a "hall of shame" on the profile:
```
taxi_accident     = plane lost, loss_cause self, and no takeoff (AType 5) and no air start before the loss
strafed_on_ground = plane lost, loss_cause attacker, the aircraft on the ground at the loss, and either
                    (a) it never took off (then it is strafed whatever hit it), or
                    (b) it landed (an AType 6 strictly before the loss) and has not taken off since, and the destroyer is an attacker:
                        the kill line names one, or an attacker hit or damaged it after the landing. Air damage before the landing
                        does not matter any more (2026-10-04, maintainer; before: every attacker line had to follow the landing).
                    Not strafed: a crash-landing (its loss resolves at the landing tick, so it is no landing here: shot up, crash-landed,
                    destroyed = shot down), and a landed wreck that burned down with no attacker line after the landing (= shot down)
both false for gunners; derived from the final is_plane_lost / loss_cause, so a disconnect death on the ground can be a taxi accident
```
Samples (15,245 pilot sorties): **767 taxi accidents** (median 207 s after spawn, ~1 km from the spawn point: taxi and takeoff-run crashes,
not parked aircraft) and **28 strafed** (10 parked, 18 after landing; the 2026-10-04 rule change added 9, all after landing). The timeline's `destroyed` / `shot_down` entry says which.

**Structural failure** (FR-ING-17 v2) uses the first `AID:-1` damage line on the aircraft and the first wheels-on / landing after the AType 3
(none = not flagged).

### Outcome (`judge._outcome`)

```
Was the aircraft lost (is_plane_lost)?
├─ yes → shot_down if loss_cause = attacker, else crashed
└─ no: Did it ever take off?
   ├─ no  → not_taken_off
   └─ yes: Forced by mission end?            (the state the mission end found it in; flag ended_by_mission_end)
      ├─ yes → airborne if in the air at the first AType 7, else landed / ditched (below)
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
  assist (il2_stats gave only the second damager an assist). **Assists are split by victim kind** (2026-10-04): `assists_air` (the victim is an
  aircraft) and `assists_ground`, and `assists` is always their sum. **Only air assists score** (`air_assist` in `[score]`); ground assists score
  nothing and are shown in the profile's air-to-ground part (air assists in the air part); the sortie page lists both. The split is stored per
  sortie and on every counter table. Upgraded databases: `ops/migrate.py::_check_assist_split` (marker `assist_split`) derives it once from the
  stored timelines' `assist` entries with the replay's own rule (a player's aircraft or an air-class object is air; an assist the timeline lost
  counts as ground), then level 2 is rebuilt (rescoring); `il2ks reprocess` gives the same. Damage to the pilot bot and turrets counts as damage to the aircraft. `via` =
  `direct`, `abandoned_aircraft` (bailout or ground exit) or `disconnect`. There is no shared credit.
- A disconnect or bailout without an AType 3 for the aircraft still creates the victim record (at sortie end), so damage-based credit applies.
- **Victims**: every destroyed object except crew, equipment (parachutes, ejection seats, spotters, vehicle turrets), gunner turrets and
  ordnance. **Static objects count as
  ground kills** (maintainer: keep everything, score them low later).
- **Ground-kill categories** `[DECIDED]` (OQ-33, 2026-10-03): every ground object in the catalog has a category, shown as a collapsible
  breakdown next to the total: `tank`, `vehicle` (trucks, cars, halftracks, tractors, rocket launchers), `artillery`, `aaa` (incl. flak
  cars), `ship` (incl. static ships and boats), `train` (locomotives, wagons, static wagons), `building` (barracks, hangars, warehouses,
  factories, bridges, towers, radars, depots, storage tanks), `parked_aircraft`, `other` (fences, crate and barrel yards, logs, camo nets,
  small fuel tanks, carts, equipment, decorations). **Static vs. moving is a separate axis**: a static truck is a `vehicle` and static.
  Unknown ground types count as `other`, not static. The categories always sum to the ground-kill total (tested), and
  `kills_ground_static` counts the static ones. Samples: other 50.8%, building 27.6%, vehicle 13.0%, aaa 3.5%, train 2.8%, parked aircraft
  0.8%, tank 0.6%, ship 0.6%, artillery 0.2%; **91.6% of ground kills are static objects** (the top player: 6,990 ground kills, 6,673
  static). Judgement calls to review: ammo storage is `other`, storage tanks and cisterns are `building`.
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
  `sortie_end`, and the **hit rows** `hit_given` / `hit_taken` (below); each with time, position and counterpart. The killer is named for
  gunners too.

### Timeline hits (2026-10-04, `core/replay/hits.py`, OQ-108) `[PROPOSED]`
The significant damage a sortie gave and took, as timeline rows (maintainer: a damage % column, significant hits as rows, ammo matched to the nearest
hit). Input: every damage line (AType 2) that touched a player sortie, each already labelled with the ammo of the **closest hit** (the ammo
attribution rule, `ammo_window_s` 1 s).
- **Burst**: the lines of one direction (given or taken) and one counterpart form a burst while each is within `hit_burst_gap_s` (3 s) of the one
  before, and a burst lasts at most `hit_burst_max_s` (15 s). A burst becomes one row at its first line's time.
- **Significant** = the burst's summed damage is at least `hit_min_damage` (0.002 of an object, **0.2%**; the maintainer suggested "e.g. over
  0.1%"). Static scenery (class `static`: fences, tents, stacks) as a target never gives a row: strafing an airfield makes thousands of tiny lines
  (30,000 rows over 1,200 sorties at 0.5%). At most 150 rows per sortie, the heaviest kept (the busiest of 1,182 measured sorties had 77).
- **Ammo of a row** = the label with the most damage among its lines (the earlier wins a tie); a line with no hit in the window has none and does not
  vote, a row where no line has one shows no ammo. So the rows agree with the ammo breakdown, which tallies the same labels. Gun ammo, ordnance
  (bombs, rockets) or other named ammo (flares).
- Row fields: `damage` (summed DMG fraction), `lines`, `ammo`, `ammo_kind` (doc 14). The page shows a signed damage % column (+ given, − taken).
  Applies on `il2ks reprocess` (older sorties have no hit rows).
- "Took off", takeoffs and flight time all stop at the aircraft's loss, so they can't disagree.
- `takeoff` time of an air start = the spawn time (`takeoffs` counts real AType 5 only). `flight_time_s` = sum of airborne intervals. Both stop at
  the aircraft's loss: logs write a "landing" for a falling wreck.

## Ammo and resupply

- v1 stores per ammo type: loaded (AType 10), left (AType 4), used (loaded − left, or unknown, see below) and hits. The damage-per-ammo attribution of FR-WEB-18 is it1.x.
- **Resupply** (FR-ING-24): a player can land, rearm and take off again in one sortie, so "loaded − left" undercounts what was fired, and
  AType 24 gun bursts carry no round count. The log has **no resupply event** (doc 12), so it's inferred `[PROPOSED]`: with
  `replay.resupply_allowed = true` (default; most servers allow it), a landing followed by another takeoff in the same sortie marks the sortie
  `resupplied`, and "ammo used" is unknown for it, bombs and rockets with no release event aside (below; hits, releases and rocket salvos are still exact). With `false`, "loaded − left" is used.
  "Used" is also unknown for bombs when "left" exceeds "loaded" (IL-10 bomblet payloads count stations when loaded and bomblets when left).
  About 2% of sorties take off more than once.
- **Ammo left after a loss** `[PROPOSED]` (2026-10-03, extended 2026-10-04): when the sortie ended more than `ammo_left_after_loss_s` (1 s) after its
  aircraft was destroyed (the pilot bailed out, climbed out or disconnected later), AType 4's "left" is unreliable (unfired stores read as zero in
  20-60% of such cases, and in all 107 checked bailouts and ground exits with unreleased stores). The sortie is flagged `left_after_loss`, "left" is
  shown as a dash and "used" is no longer computed as loaded - left (2,807 sample sorties). When the pilot died with the aircraft (AType 4 within
  1 s), "left" is right about 90% of the time and is used.
- **Used where "left" cannot be trusted** (after a loss, resupplied, no AType 4, or bombs with more left than loaded), for **bombs and rockets**
  (`ingest/persist.py::_ammo_used`): AType 25 (store) and AType 26 (rocket salvo) events are release *commands*, not counts (one event can drop a pair
  of bombs, a rocket event is a salvo), so they cannot give the number used. But **no release event in the whole sortie means none was used**: used = 0.
  One or more releases leave "used" unknown (OQ-101: right in 87-90% of comparable sorties; a "~all loaded" estimate was the alternative). **Gun
  ammo** (bullets, shells) stays unknown in these cases. Where "left" is trusted it is kept even if it disagrees with the releases. The counts are
  stored as `ammo.releases` (`stores`, `rocket_salvos`) and `ammo.left_after_loss`; the sortie page words it "the ammunition left was recorded after
  the aircraft was lost" (the game writes the record when the sortie ends).

## Mission end (as built, 2026-10-03)

The maintainer's rule: a sortie the mission end cut off reports **what state it was in**: outcome `airborne` (1,235 sample sorties),
`landed` (36) or `ditched` (6) by the usual landing rule, or `not_taken_off`; pilot fate `in_aircraft`; flag `ended_by_mission_end`;
the timeline's `sortie_end` says "mission end". A loss before the mission end is still `shot_down` / `crashed`. Forced gunner sorties
take the state of their aircraft. Existing databases: migration 0011 maps the old values conservatively (old outcome `mission_ended`
→ `airborne` when the counts show the aircraft was in the air, else `unknown`); `il2ks reprocess --all` gives the exact values.
Destruction before a disconnect (OQ-36): 10 sample sorties changed (4 shot down, 6 crashed; 10 more deaths and losses), nothing else.

## PvE breakdown (FR-WEB-21, as built 2026-10-03)

- Every lost sortie gets one **loss class**, from the existing verdict (never re-derived): `loss_cause = self` → `environment` (crash,
  terrain, structural failure, own error, an abandoned aircraft nobody hit; pages label it "No attacker"); `attacker` → the class of the
  credited killer (or of the party with the most hits when credit named nobody: 45 of 5,842 losses), checked in this order: `friendly`
  (same non-zero coalition, player or AI), `player`, `ai_aircraft` (AI fighter / attacker), `ai_gunner` (AI bomber / transport: their only
  weapons are defensive guns, and the log credits turret fire to the aircraft, so this goes by aircraft type), `aaa` (incl. flak cars),
  `ground` (tanks, vehicles, ships, statics), else `unknown`.
- Counters `deaths_by_<class>` and `planes_lost_by_<class>` (sum to `deaths` / `planes_lost` by construction), and air kills split into
  `kills_air_pvp` + `kills_air_ai` (= `kills_air`).
- Samples (4,385 deaths): no attacker 54.5%, player 33.6%, AAA 8.9%, ground 1.7%, friendly 1.0%, AI gunners 0.3%, AI fighters 0 (the
  samples have no AI fighters). AAA causes 21% of F-51D and 19% of IL-10 deaths but 5% of MiG-15bis deaths. 93% of air kills are PvP.

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

**Interception and tank busting** (2026-10-04, maintainer: two skill boards as visible as Elo and ground per hour; definitions `[PROPOSED]`,
OQ-102, OQ-103). Both are per-hour rates of stored counters, computed at read time; the counters are sums over a pilot's sorties.
- **Interception** = kills of bombers and attackers **per hour of air superiority flight**. A kill counts when it is a credited air kill (not an
  assist, not friendly; PvP or AI) whose victim is an **interception victim** (`attack.is_interception_victim`): an AI aircraft of catalog class
  `bomber` or `attacker`, or a player sortie with the combat role `attack` (a fighter carrying bombs or rockets). Transports are not victims.
  `PlayerSortie.kills_air_intercept` is a part of `kills_air`. The counters `kills_intercept` (the kills made **in air superiority sorties**),
  `flight_time_air_s` and `air_superiority_sorties` sum the pilot's air superiority sorties; an attacker shooting a bomber from an attack sortie does
  not count. Board minimum: `[score] min_air_superiority_sorties` (5) and `min_air_superiority_minutes` (60).
- **Tank busting** = **tanks destroyed in attack sorties per hour on target** (`kills_tank_attack` / `time_on_target_s`). A tank is a ground kill of
  category `tank` (static or moving). Board minimum: the ground-per-hour minimums (`min_attack_sorties` 5, `min_time_on_target_minutes` 10).
- Upgraded databases: `ops/migrate.py::_backfill_interception` derives `kills_air_intercept` once from the stored sortie timelines (victim types and
  victim sortie roles), then rebuilds level 2 (marker `interception` in `SiteSettings.backfills_done`); `il2ks reprocess` gives the same.

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
- **Per-type Elo** (OQ-49, built 2026-10-04, `[PROPOSED]`): the same games and the same replay also give a rating per (player, aircraft type),
  stored on `PlayerAircraft` (`elo`, `elo_games`). Each starts at `[ratings] start`; the opponent's rating is their rating in the type they flew
  in that game. Pools still apply: a jet killing a prop changes nothing. A type's top pilots are ranked by it (FR-WEB-8).

## Score (as built, 2026-10-04)

Pure function `score_sortie` in `core/ratings/score.py`, per pilot sortie (gunners 0); stored as `PlayerSortie.air_points` / `ground_points`
and summed into `score_air` / `score_ground` / `score_ground_attack` (doc 06). Everything comes from stored sortie columns and the `[score]`
section, so a changed rule applies with `il2ks rebuild-aggregates`, no reprocess. Air and ground score are never combined (OQ-62).
- **Points**: per air kill (PvP aircraft more than an AI one), per assist, and one value per ground-kill category (fences worth very little).
- **Outcome penalties are percentages** (`[score] penalty_*_pct`, in percent, clamped 0..100; the old flat keys `penalty_death` / `penalty_plane_lost` / `penalty_capture` are gone and ignored, OQ-100): **death 80%,
  capture 50%, aircraft lost without death or capture 20%**. The percentage comes off **both** the air and the ground score, only from a
  positive score (never below 0), and when several apply the **largest** one counts (OQ-67, decided with these defaults).
- **Flat penalties** come off afterwards, from the score of the sortie's combat role (attack: ground score; otherwise air score): a suspected
  early bailout (5) and each friendly kill (3, up to 5 kills per sortie). A sortie with a flat penalty and no kills can be negative.
- **Leaderboard minimums** (`[score] min_sorties` 5, `min_elo_games` 5, `min_attack_sorties` 5, `min_time_on_target_minutes` 10,
  `min_air_superiority_sorties` 5, `min_air_superiority_minutes` 60) apply at read time. Old flat penalty keys warn at load (FR-OPS-2).

### Stat marks (FR-WEB-22, `core/stat_marks.py`, as built 2026-10-04)
A mark says where a figure stands among the pilots, by the percentiles stored in `StatThreshold` (strictly above p90 = "Top 10%", above p75 = "Top
25%"; low values never marked; no distribution under 20 pilots). Which pilots form a metric's population follows the board the figure sits next to:
the ratios and the air and ground scores need `[marks] min_sorties` (20) sorties; **Elo jet / Elo prop** need `min_elo_games` rated games in that pool
(all time only, shown against the all-time population on a tour profile too); **ground score per hour and tanks per hour** need
`min_time_on_target_s` on target; **interception per hour** needs `min_air_superiority_s` of air superiority flight. `StatThreshold.min_sorties`
stores that minimum in the metric's own unit (sorties, games, seconds).

## Rule toggles (`[rules]`, as built 2026-10-04, OQ-61)

The ram toggle applies via `il2ks reprocess --all` (it changes kills and deaths, not only aggregates).
- **Rams** `[PROPOSED]`: the log has no collision event. A ram is two aircraft destroyed while airborne, before the first AType 7, within
  `ram_window_s` (**0.5 s**) and `ram_distance_m` (**15 m**) of each other (maintainer, OQ-92, 2026-10-04: the tighter values; the first values
  2 s / 50 m let 4 looser cases through among the 17 below), where neither has an attacker to blame and neither hit the other with
  guns. With `[rules] credit_rams = true` (default **false**) each aircraft of an enemy pair is credited a kill for the other (the victim's
  loss is then `attacker` / `shot_down`, `via direct`). A collision between friends credits nobody and has no friendly-kill penalty.
  Validated on the 210 sample missions (at 2 s / 50 m): 17 rams, 13 between enemies (26 kills), 4 between friends, no false positive identified; about 35
  debris collisions correctly excluded; about 10 to 15 rams with prior third-party damage missed (damage-based credit already gives them to
  someone). Low-altitude ground crashes can't be told apart without terrain height (OQ-39). Code: `core/replay/rams.py`.
- **Parachute deaths: no toggle** (maintainer, OQ-99, 2026-10-04): the toggle `parachute_deaths` was removed; a pilot killed while parachuting is
  always a death. An `il2ks.toml` that still sets it gets a warning at load (the key is ignored).
- Product choices behind them: OQ-89, OQ-90, OQ-92, OQ-99.
