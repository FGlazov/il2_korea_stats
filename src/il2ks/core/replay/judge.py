"""Put the per-sortie rules together into one `Verdict` (TD-16: the rules themselves live in `fate.py`)."""

from dataclasses import dataclass

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.areas import at_friendly_airfield, on_enemy_territory
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.credit import is_self_attack, unit_objects
from il2ks.core.replay.fate import (
    Loss,
    aircraft_loss,
    attacker_involved,
    bailout_v3,
    disconnect_death,
    disconnect_tick_of,
    final_pos_pending,
    flight_time_s,
    forced_by_mission_end,
    ground_loss,
    killer_of,
    left_before,
    pilot_death_tick,
    pilot_fate_of,
    pilot_final_pos,
    sortie_end_tick,
    structural_failure,
    suspected_early_bailout,
    took_off,
)
from il2ks.core.replay.model import MissionFacts, Party, SortieState
from il2ks.core.replay.result import (
    AircraftStatus,
    LossCause,
    Outcome,
    PilotFate,
    PilotFateSource,
    PilotStatus,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    end_tick: int
    loss: Loss | None  # None also when a disconnect without recent damage ignores a later destruction
    died_tick: int | None
    disconnect_tick: int | None
    took_off: bool
    flight_time_s: float
    fate: PilotFate
    fate_source: PilotFateSource
    bailout: bool
    suspected_early_bailout: bool
    disconnected: bool
    disconnect_death: bool
    is_death: bool
    is_plane_lost: bool
    is_captured: bool
    loss_cause: LossCause
    killer: Party | None  # who gets the kill for the loss or crew death; None unless `loss_cause` is `attacker`
    structural_failure: bool
    taxi_accident: bool  # OQ-32: lost before the first takeoff to nobody but itself
    strafed_on_ground: bool  # OQ-32: lost on the ground to an attacker
    outcome: Outcome
    pilot_status: PilotStatus
    aircraft_status: AircraftStatus
    damage_taken: float
    cutoff_tick: int  # damage and hits after this tick don't belong to the sortie (the loss, the end, or AType 7 - 1)
    ended_by_mission_end: bool  # the server force-ended the sortie at mission end (`outcome` is the state then)
    active_end_tick: int  # the sortie end, or the loss tick when the aircraft was destroyed first (flight stops there)


def _landing_pos(sortie: SortieState, end_tick: int) -> Pos | None:
    landings = [pos for t, pos in sortie.airframe.landings if sortie.spawn_tick <= t <= end_tick]
    return landings[-1] if landings else None


def judge(sortie: SortieState, facts: MissionFacts, rules: ReplayRules, *, final: bool) -> Verdict:
    airframe = sortie.airframe
    end = sortie_end_tick(sortie, facts)
    disc_tick = disconnect_tick_of(sortie, facts, rules)
    loss = aircraft_loss(sortie, facts, rules, disc_tick)
    died = pilot_death_tick(sortie, facts, rules)
    forced = forced_by_mission_end(sortie, facts, rules, final=final)
    mission_end = facts.first_mission_end
    if forced and mission_end is not None:
        # The server despawns everything at mission end: destruction from that tick on is cleanup, not combat (doc 12)
        loss = loss if loss is not None and loss.tick < mission_end else None
        died = died if died is not None and died < mission_end else None
        forced = loss is None and died is None
    found = bailout_v3(sortie, loss, died, end, rules)
    bailout = found.detected
    shot_down_directly = loss is not None and loss.by is not None and not is_self_attack(loss.by, airframe, sortie)

    fate, source = pilot_fate_of(
        sortie=sortie,
        forced=forced,
        bailout=bailout,
        exit_pos_known=found.exit_pos_known,
        by_ejection_spawn=found.by_ejection_spawn,
        disconnect_tick=disc_tick,
        dead=died is not None,
    )
    if sortie.is_open and not forced:
        fate, source = ("in_aircraft", "inferred") if not final else ("unknown", "unknown")
    elif not final and not forced and died is None and final_pos_pending(sortie, rules):
        # Live snapshot between AType 4 `PLID:0` and the pilot's AType 16: the fate waits for the position, so it reads
        # like an open sortie (the pilot is still assumed to be in the aircraft) instead of a final `unknown`.
        fate, source = "in_aircraft", "inferred"
    # FR-ING-21/22: a disconnect doesn't hide an attacker's kill. The fate says `disconnected` (maintainer), but the
    # aircraft destroyed by an attacker stays a loss and the pilot died with it, exactly as for any plain sortie end.
    attacker_destroyed = fate == "disconnected" and shot_down_directly
    # OQ-36: an aircraft destroyed before the player's exit is a normal loss, however long before it was (the player
    # pressed "exit server" instead of "end sortie"); the pilot died with it unless they had already left it.
    destroyed_before_exit = fate == "disconnected" and loss is not None and loss.tick <= end
    pilot_aboard = destroyed_before_exit and loss is not None and not left_before(sortie, loss.tick)
    if (fate == "in_aircraft" or attacker_destroyed or pilot_aboard) and loss is not None and not forced:
        died = died if died is not None else loss.tick  # the pilot went down with the aircraft

    disconnected = disc_tick is not None or sortie.ended_by_removal
    disc_death = (
        fate == "disconnected"
        and not attacker_destroyed
        and not destroyed_before_exit
        and disconnect_death(sortie, disc_tick if disc_tick is not None else end, rules)
    )
    if fate == "disconnected" and not disc_death and not attacker_destroyed and not destroyed_before_exit:
        loss = None  # FR-ING-21: a disconnect without recent damage is neither a death nor a loss
        died = None

    rammer = facts.ram_partners.get(id(airframe)) if loss is not None else None  # `credit_rams`, rams.ram_partners
    cutoff = loss.tick if loss is not None else end
    active_end = min(end, cutoff)
    # Damage the server logs at or after AType 7 is the despawn cleanup (doc 12, 13): a forced sortie keeps none of it.
    damage_cutoff = min(cutoff, mission_end - 1) if forced and mission_end is not None else cutoff
    off = took_off(sortie, active_end)  # same bound as the takeoffs and flight time that `resolve` reports
    lost = loss is not None or died is not None or bailout or disc_death
    attacker_cause = lost and (shot_down_directly or rammer is not None or attacker_involved(sortie, cutoff))
    loss_cause: LossCause = "none" if not lost else ("attacker" if attacker_cause else "self")
    structural = loss is not None and lost and structural_failure(sortie, loss, attacker_cause, rules)

    taxi, strafed = ground_loss(sortie, lost=lost, loss_cause=loss_cause, cutoff_tick=cutoff)

    dead = died is not None or disc_death
    status_pos: Pos | None = None
    if not dead and not forced:
        if fate in ("bailed_out", "exited_on_ground"):
            status_pos = pilot_final_pos(sortie, rules)
        elif off and not airframe.airborne_at(end):
            status_pos = _landing_pos(sortie, end) or airframe.pos
    areas = list(facts.areas.values())
    captured = status_pos is not None and on_enemy_territory(status_pos, sortie.coalition, areas)

    damage_taken = min(1.0, sum(r.amount for r in airframe.damage_log if r.tick <= damage_cutoff))
    bot_damage = any(r.tick <= damage_cutoff for obj in unit_objects(sortie.bot) for r in obj.damage_log)
    pilot_status: PilotStatus = "dead" if dead else "captured" if captured else "wounded" if bot_damage else "healthy"
    aircraft_status: AircraftStatus = "destroyed" if loss is not None else "damaged" if damage_taken > 0 else "unharmed"

    outcome = _outcome(sortie, facts, end, lost=lost, attacker_cause=attacker_cause, off=off, forced=forced)
    return Verdict(
        end_tick=end,
        loss=loss,
        died_tick=died,
        disconnect_tick=disc_tick,
        took_off=off,
        flight_time_s=flight_time_s(sortie, active_end),
        active_end_tick=active_end,
        fate=fate,
        fate_source=source,
        bailout=bailout,
        suspected_early_bailout=suspected_early_bailout(
            sortie, facts, rules, bailout=bailout, upto_tick=cutoff, disconnected=disc_tick is not None
        ),
        disconnected=disconnected,
        disconnect_death=disc_death,
        is_death=dead,
        is_plane_lost=lost,
        is_captured=captured,
        loss_cause=loss_cause,
        killer=killer_of(sortie, loss, died, cutoff, rules.assist_min_damage, rammer)
        if loss_cause == "attacker"
        else None,
        structural_failure=structural,
        taxi_accident=taxi,
        strafed_on_ground=strafed,
        outcome=outcome,
        pilot_status=pilot_status,
        aircraft_status=aircraft_status,
        damage_taken=damage_taken,
        cutoff_tick=damage_cutoff,
        ended_by_mission_end=forced,
    )


def _outcome(
    sortie: SortieState,
    facts: MissionFacts,
    end: int,
    *,
    lost: bool,
    attacker_cause: bool,
    off: bool,
    forced: bool,
) -> Outcome:
    """Derived from il2_stats (MIT), see NOTICE: Sortie.sortie_status, extended for the Korea fates."""
    airframe = sortie.airframe
    if lost:
        return "shot_down" if attacker_cause else "crashed"
    if not off:
        return "not_taken_off"
    mission_end = facts.first_mission_end
    if forced and mission_end is not None:
        # The aircraft's state when the mission ended: the cleanup after AType 7 is not part of the sortie
        if airframe.airborne_at(mission_end):
            return "airborne"
        return _landed_or_ditched(sortie, facts, mission_end)
    if sortie.is_open:
        return "in_flight" if airframe.airborne else "landed"
    if sortie.airborne_at_end:
        # A closed sortie never stays `in_flight`: a disconnected pilot, or a gunner who left (AType 4, parent still
        # flying) with the aircraft in the air. What became of them afterwards is not in this sortie's log.
        return "unknown"
    return _landed_or_ditched(sortie, facts, end)


def _landed_or_ditched(sortie: SortieState, facts: MissionFacts, end: int) -> Outcome:
    pos = _landing_pos(sortie, end)
    if pos is None:
        return "landed"
    airfields = list(facts.airfields.values())
    if not any(a.coalition == sortie.coalition for a in airfields):
        return "landed"  # no friendly airfield logged: can't tell, so don't call it a ditching
    return "landed" if at_friendly_airfield(pos, sortie.coalition, airfields) else "ditched"
