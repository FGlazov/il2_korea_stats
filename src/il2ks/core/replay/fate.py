"""Per-sortie rules: how the aircraft and pilot ended (FR-ING-14, -17, -21). One function per rule (TD-16).

Everything reads recorded facts, so the same code serves `snapshot()` and `finish()`. Ticks are 50 per second.
"""

from dataclasses import dataclass

from il2ks.core.logparse.events import TICKS_PER_SECOND, Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.credit import credit_kill, damage_records, hit_records, unit_objects
from il2ks.core.replay.model import MissionFacts, Party, SortieState, TrackedObject, distance, party_of
from il2ks.core.replay.result import LossCause, PilotFate, PilotFateSource


def ticks(seconds: float) -> int:
    return round(seconds * TICKS_PER_SECOND)


@dataclass(frozen=True, slots=True)
class Loss:
    """The sortie's aircraft was destroyed (AType 3) within the sortie's scope."""

    tick: int
    pos: Pos | None
    airborne: bool
    by: TrackedObject | None  # the AID of the kill line; None = environment (AID:-1)


def sortie_end_tick(sortie: SortieState, facts: MissionFacts) -> int:
    return sortie.end_tick if sortie.end_tick is not None else facts.last_tick


def left_aircraft(sortie: SortieState, disconnect_tick: int | None) -> bool:
    """The pilot isn't in the aircraft when the sortie ends: AType 4 `PLID:0`, no AType 4 at all, or a disconnect."""
    return sortie.end_aircraft_id == 0 or sortie.ended_by_removal or disconnect_tick is not None


def left_before(sortie: SortieState, tick: int) -> bool:
    """Evidence that the pilot was out of the aircraft before `tick` (a bailout event, or the bot already removed)."""
    bot = sortie.bot
    return (bot.bailout_tick is not None and bot.bailout_tick <= tick) or (
        bot.removed_tick is not None and bot.removed_tick < tick
    )


def disconnect_tick_of(sortie: SortieState, facts: MissionFacts, rules: ReplayRules) -> int | None:
    """The AType 21 of this account nearest to the sortie end, within `disconnect_window_s` (FR-ING-14 cond. 6)."""
    end = sortie_end_tick(sortie, facts)
    window = ticks(rules.disconnect_window_s)
    near = [t for t in sortie.disconnect_ticks if end - window <= t <= end + window]
    return min(near, key=lambda t: abs(t - end)) if near else None


def post_end_window_ticks(sortie: SortieState, rules: ReplayRules) -> int:
    """How long after the sortie end a destruction still counts (OQ-30). The long window is for the shot-down shape
    (the aircraft was in the air when the sortie ended); a parked or taxiing aircraft gets the short one, so a pilot who
    landed and left isn't killed by a later destruction of the parked aircraft or of a reused log ID.

    A gunner always gets the short one: the aircraft keeps flying after the gunner left it, so its destruction minutes
    later (or its cleanup at the mission end) is not the gunner's death (2 gunner sorties in the 210 samples)."""
    airborne = sortie.airborne_at_end and sortie.role == "pilot"
    return ticks(rules.post_end_destroy_window_s if airborne else rules.post_end_destroy_window_ground_s)


def aircraft_loss(
    sortie: SortieState, facts: MissionFacts, rules: ReplayRules, disconnect_tick: int | None
) -> Loss | None:
    """Rule: aircraft destruction counts for the sortie if it happens before the end, shortly after it (the shot-down
    shape logs AType 4 first, doc 12), or at any time when the pilot left an airborne aircraft (FR-ING-22)."""
    airframe = sortie.airframe
    destroyed = airframe.destroyed_tick
    if destroyed is None:
        return None
    if sortie.end_tick is not None and destroyed > sortie.end_tick + post_end_window_ticks(sortie, rules):
        abandoned = sortie.airborne_at_end and left_aircraft(sortie, disconnect_tick)
        if not abandoned:
            return None
    return Loss(destroyed, airframe.destroyed_pos, airframe.destroyed_airborne, airframe.destroyed_by)


def crew_death(sortie: SortieState) -> TrackedObject | None:
    """The object whose AType 3 is this sortie's crew member dying: the pilot or gunner bot, and for a gunner who
    didn't bail out also its turret (design_doc/13_game_rules.md, Gunners). Earliest destruction wins."""
    candidates = [sortie.bot]
    if sortie.role == "gunner" and sortie.bot.bailout_tick is None:
        candidates.append(sortie.vehicle)
    dead = [obj for obj in candidates if obj.destroyed_tick is not None]
    return min(dead, key=lambda obj: obj.destroyed_tick or 0) if dead else None


def killer_of(
    sortie: SortieState,
    loss: Loss | None,
    died_tick: int | None,
    upto_tick: int,
    assist_min_damage: float,
    rammer: TrackedObject | None = None,
) -> Party | None:
    """Who gets the kill for this sortie's loss or crew death (the same `credit_kill` as the KillResults), for naming
    the killer on the sortie's own timeline. A gunner sortie has no KillResult of its own (design_doc/13_game_rules.md,
    Gunners), so this is its only source. `None` when nobody but the environment or the sortie itself is responsible,
    except that the aircraft that rammed this one (`credit_rams`, `rammer`) is then the killer."""
    explicit = loss.by if loss is not None else None
    if explicit is None and died_tick is not None:
        crew = crew_death(sortie)
        explicit = crew.destroyed_by if crew is not None else None
    credited = credit_kill(sortie.airframe, sortie, explicit, upto_tick, assist_min_damage)
    if credited:
        return credited[0].party
    return party_of(rammer) if rammer is not None else None


def pilot_death_tick(sortie: SortieState, facts: MissionFacts, rules: ReplayRules) -> int | None:
    """The pilot (or gunner) was killed (AType 3) during the sortie or right after its end."""
    obj = crew_death(sortie)
    died = obj.destroyed_tick if obj is not None else None
    if died is None:
        return None
    if sortie.end_tick is not None and died > sortie.end_tick + post_end_window_ticks(sortie, rules):
        return None
    return died


def forced_by_mission_end(sortie: SortieState, facts: MissionFacts, rules: ReplayRules, *, final: bool) -> bool:
    """Doc 12: the server force-ends every active sortie right after AType 7 (`ended_by_mission_end`)."""
    mission_end = facts.first_mission_end
    if mission_end is None:
        return False
    if sortie.end_tick is None:
        return final
    normal_end = sortie.end_aircraft_id is not None and sortie.end_aircraft_id != 0  # PLID:0 = the pilot left
    return normal_end and mission_end <= sortie.end_tick <= mission_end + ticks(rules.mission_end_sortie_window_s)


def took_off(sortie: SortieState, end_tick: int) -> bool:
    """AType 5, an air start, or (gunner) joining an aircraft that was already airborne."""
    airframe = sortie.airframe
    if airframe.airborne_at(sortie.spawn_tick):
        return True
    return any(airborne and sortie.spawn_tick <= t <= end_tick for t, airborne in airframe.flight_changes)


def ground_loss(sortie: SortieState, *, lost: bool, loss_cause: LossCause, cutoff_tick: int) -> tuple[bool, bool]:
    """OQ-32: `(taxi_accident, strafed_on_ground)` for a lost pilot aircraft (crashes before takeoff still count as
    deaths and losses; these two flags only label them). `cutoff_tick` is the loss, else the sortie end.

    - taxi accident: the aircraft never took off and nobody else is to blame (`loss_cause` self). Air starts never
      qualify.
    - strafed on the ground: an attacker is to blame and the aircraft was on the ground, either it never took off, or it
      had landed and not taken off again, and every attacker hit or damage on the aircraft or crew came after that
      landing (a shot-up aircraft that crash-lands and is then destroyed was shot down, not strafed).

    Gunners are never flagged: their flight state is the parent's, and gunners aren't counted."""
    if sortie.role != "pilot" or not lost:
        return False, False
    airframe = sortie.airframe
    if loss_cause == "self":
        return not took_off(sortie, cutoff_tick), False
    if loss_cause != "attacker" or airframe.airborne_at(cutoff_tick):
        return False, False
    landed_at = max((t for t, airborne in airframe.flight_changes if not airborne and t <= cutoff_tick), default=-1)
    attacks = [r.tick for r in damage_records(airframe, sortie, cutoff_tick, attackers_only=True)]
    attacks += [r.tick for r in hit_records(airframe, sortie, cutoff_tick, attackers_only=True)]
    return False, all(t > landed_at for t in attacks)


def was_resupplied(takeoff_ticks: list[int], landing_ticks: list[int], rules: ReplayRules) -> bool:
    """FR-ING-24: some AType 6 is followed later by an AType 5 in this sortie (the aircraft may have been rearmed)."""
    if not rules.resupply_allowed or not landing_ticks:
        return False
    first_landing = min(landing_ticks)
    return any(t > first_landing for t in takeoff_ticks)


def flight_time_s(sortie: SortieState, end_tick: int) -> float:
    """Sum of airborne intervals inside the sortie (AType 5 to AType 6, or to the end)."""
    airframe = sortie.airframe
    airborne = airframe.airborne_at(sortie.spawn_tick)
    since = sortie.spawn_tick
    total = 0
    for t, state in airframe.flight_changes:
        if t <= sortie.spawn_tick or t > end_tick:
            continue
        if state and not airborne:
            since, airborne = t, True
        elif not state and airborne:
            total += t - since
            airborne = False
    if airborne:
        total += end_tick - since
    return total / TICKS_PER_SECOND


def pilot_final_pos(sortie: SortieState, rules: ReplayRules) -> Pos | None:
    """Where the pilot ended up: the AType 16 position (garbage positions are already dropped, `is_plausible_pos`), else
    the bot's latest AType 12 position near the sortie end, else unknown."""
    bot = sortie.bot
    if bot.removed_pos is not None:
        return bot.removed_pos
    end = sortie.end_tick
    if end is None or bot.declared_tick is None or bot.declared_pos is None:
        return None
    return bot.declared_pos if abs(bot.declared_tick - end) <= ticks(rules.pilot_pos_fallback_window_s) else None


def final_pos_pending(sortie: SortieState, rules: ReplayRules) -> bool:
    """The pilot left the aircraft (AType 4 `PLID:0`) but no final position is known. AType 16 follows AType 4 within
    0.34 s, so a live snapshot can land in the gap; at `finish()` it is a real gap."""
    return sortie.end_aircraft_id == 0 and pilot_final_pos(sortie, rules) is None


def bailout_v2(sortie: SortieState, loss: Loss | None, died_tick: int | None, rules: ReplayRules) -> tuple[bool, bool]:
    """FR-ING-14 rule v2: (is_bailout, pilot_had_exit_pos). All of conditions 1-4 must hold.

    Returns `(False, False)` when the pilot's final position is unknown, so the caller can report `unknown`."""
    if sortie.end_aircraft_id != 0:  # 1. AType 4 PLID:0
        return False, True
    airframe = sortie.airframe
    airborne = loss.airborne if loss is not None else sortie.airborne_at_end  # 2.
    if not airborne:
        return False, True
    pilot_pos = pilot_final_pos(sortie, rules)
    aircraft_pos = loss.pos if loss is not None and loss.pos is not None else airframe.pos
    if pilot_pos is None or aircraft_pos is None:
        return False, False
    if distance(pilot_pos, aircraft_pos) < rules.bailout_min_distance_m:  # 3.
        return False, True
    if loss is not None and died_tick is not None and abs(died_tick - loss.tick) <= ticks(rules.died_with_aircraft_s):
        return False, True  # 4. died together with the aircraft
    return True, True


def ejection_spawn_tick(sortie: SortieState, end_tick: int, rules: ReplayRules) -> int | None:
    """Ejection spawn (method 1 by Rufus, developer of another IL-2 Korea stats system, shared via the maintainer): the
    log writes an AType 12 for the pilot body that reuses the player's bot ID (the AType 10 `PID:`) with `PID:-1`, i.e.
    detached from the aircraft (re-announcements of a seated bot carry the aircraft ID). Returns its tick, or `None`.

    Gates: the aircraft is airborne by AType 5/6 state (the same spawn fires for a pilot climbing out after landing),
    and the pilot didn't die at that moment (a pilot killed in the seat is announced detached too: 49 of 378 in the
    210 samples, every one of them with the pilot's AType 3 within 0.5 s)."""
    if sortie.role != "pilot":
        return None
    died = sortie.bot.destroyed_tick
    for tick, _ in sortie.bot.detached_declarations:
        if not sortie.spawn_tick <= tick <= end_tick + 1 or not sortie.airframe.airborne_at(tick):
            continue
        if died is not None and died - tick <= ticks(rules.died_with_aircraft_s):
            continue
        return tick
    return None


@dataclass(frozen=True, slots=True)
class Bailout:
    detected: bool
    exit_pos_known: bool  # False only when a `PLID:0` end has no usable pilot position (the fate is then `unknown`)
    by_ejection_spawn: bool  # the log itself shows the ejection (AType 12 `PID:-1`), so the fate source is `event`


def bailout_v3(
    sortie: SortieState, loss: Loss | None, died_tick: int | None, end_tick: int, rules: ReplayRules
) -> Bailout:
    """FR-ING-14 rule v3 (design_doc/13_game_rules.md): a bailout is an AType 4 `PLID:0` end and either
    1. an ejection spawn (`ejection_spawn_tick`, Rufus's method 1): the log shows the pilot leaving the airborne
       aircraft, whatever the geometry says (the live wheels flag can be stale, and a quick end-mission under the
       canopy can put the pilot within 100 m of the aircraft's last logged position), or
    2. rule v2 (`bailout_v2`) and the pilot was not already dead at the sortie end (a pilot killed in the seat is
       written as `PLID:0` at the moment of death, 296 such sorties in the 210 samples, 67 of them were suspected early
       bailouts; Rufus's method 2 has the same "pilot isn't already dead" gate).

    Rufus's method 2 as a whole (aircraft destroyed, never landed, > 200 m or > 30 m above ground) is not used: v2
    finds all but 9 of its bailouts, those 9 are ground exits after a crash landing, and the height arm needs a
    heightmap (OQ-39). See `devtools.bailout_eval`."""
    if sortie.end_aircraft_id != 0:
        return Bailout(False, True, False)
    if ejection_spawn_tick(sortie, end_tick, rules) is not None:
        return Bailout(True, True, True)
    detected, known = bailout_v2(sortie, loss, died_tick, rules)
    if detected and died_tick is not None and died_tick <= end_tick + ticks(rules.died_with_aircraft_s):
        return Bailout(False, True, False)
    return Bailout(detected, known, False)


def attacker_involved(sortie: SortieState, upto_tick: int) -> bool:
    """Any hit or damage from an attacker (not the environment, not the player themselves) on aircraft or pilot."""
    airframe = sortie.airframe
    # The sortie's own crew member counts too: a player gunner's bot is a grandchild of the aircraft
    # (design_doc/13_game_rules.md, Gunners)
    return any(
        damage_records(victim, sortie, upto_tick, attackers_only=True)
        or hit_records(victim, sortie, upto_tick, attackers_only=True)
        for victim in (airframe, sortie.bot)
    )


def suspected_early_bailout(
    sortie: SortieState, facts: MissionFacts, rules: ReplayRules, *, bailout: bool, upto_tick: int, disconnected: bool
) -> bool:
    """FR-ING-14 conditions 5-7: nobody attacked, no disconnect, not at mission end."""
    if not bailout or disconnected:
        return False
    if attacker_involved(sortie, upto_tick):
        return False
    mission_end = facts.first_mission_end
    end = sortie_end_tick(sortie, facts)
    return not (mission_end is not None and end >= mission_end - ticks(rules.mission_end_window_s))


def disconnect_death(sortie: SortieState, disconnect_tick: int, rules: ReplayRules) -> bool:
    """FR-ING-21: aircraft or pilot took damage from any source in the window before the disconnect."""
    start = disconnect_tick - ticks(rules.disconnect_damage_window_s)
    return any(
        start <= record.tick <= disconnect_tick for obj in unit_objects(sortie.airframe) for record in obj.damage_log
    )


def structural_failure(sortie: SortieState, loss: Loss, attacker_loss: bool, rules: ReplayRules) -> bool:
    """FR-ING-17 definition v2: self loss, destroyed airborne, sudden self damage, wreck kept falling."""
    if attacker_loss or not loss.airborne:
        return False
    airframe = sortie.airframe
    self_damage = [r.tick for r in airframe.damage_log if r.attacker is None and r.tick <= loss.tick]
    if not self_damage or loss.tick - min(self_damage) >= ticks(rules.structural_sudden_s):
        return False
    contact = airframe.ground_contact_after_destroyed_tick
    return contact is not None and contact - loss.tick > ticks(rules.structural_fall_s)


def pilot_fate_of(
    *,
    sortie: SortieState,
    forced: bool,
    bailout: bool,
    exit_pos_known: bool,
    by_ejection_spawn: bool,
    disconnect_tick: int | None,
    dead: bool,
) -> tuple[PilotFate, PilotFateSource]:
    """FR-ING-14 fate ladder: where the pilot ended up. Returns the fate and its source (`event`/`inferred`/`unknown`).

    A disconnect is a disconnect even when an attacker destroyed the aircraft (maintainer, like il2_stats): the death,
    the loss and the kill credit are decided elsewhere (FR-ING-21, -22), not by the fate."""
    if sortie.bot.bailout_tick is not None and sortie.role == "gunner":
        return "bailed_out", "event"  # AType 18 exists for gunners
    if dead:
        return "in_aircraft", "event"
    if forced and sortie.end_aircraft_id is not None:
        return "in_aircraft", "event"  # the pilot was still in the aircraft when the server despawned it
    if forced:
        return "in_aircraft", "inferred"
    plain_end = sortie.end_aircraft_id is not None and sortie.end_aircraft_id != 0
    disconnected = sortie.ended_by_removal or (disconnect_tick is not None and sortie.airborne_at_end and plain_end)
    if sortie.end_aircraft_id == 0:
        if bailout:
            return "bailed_out", "event" if by_ejection_spawn else "inferred"
        return ("exited_on_ground", "inferred") if exit_pos_known else ("unknown", "unknown")
    if disconnected:
        return "disconnected", "inferred"
    if plain_end:
        return "in_aircraft", "event"
    return "unknown", "unknown"  # still open
