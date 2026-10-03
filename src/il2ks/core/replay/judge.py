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
    bailout_v2,
    disconnect_death,
    disconnect_tick_of,
    flight_time_s,
    forced_by_mission_end,
    pilot_death_tick,
    pilot_fate_of,
    sortie_end_tick,
    structural_failure,
    suspected_early_bailout,
    took_off,
)
from il2ks.core.replay.model import MissionFacts, SortieState
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
    structural_failure: bool
    outcome: Outcome
    pilot_status: PilotStatus
    aircraft_status: AircraftStatus
    damage_taken: float
    cutoff_tick: int  # damage and hits after this tick don't belong to the sortie


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
    if forced and (loss is not None or died is not None):
        forced = False
    off = took_off(sortie, end)
    bailout, exit_known = bailout_v2(sortie, loss, died, rules)
    shot_down_directly = loss is not None and loss.by is not None and not is_self_attack(loss.by, airframe, sortie)

    fate, source = pilot_fate_of(
        sortie=sortie,
        forced=forced,
        bailout=bailout,
        exit_pos_known=exit_known,
        disconnect_tick=disc_tick,
        dead=died is not None,
        shot_down_directly=shot_down_directly,
    )
    if sortie.is_open and not forced:
        fate, source = ("in_aircraft", "inferred") if not final else ("unknown", "unknown")
    if fate == "in_aircraft" and loss is not None and not forced:
        died = died if died is not None else loss.tick  # the pilot went down with the aircraft

    disconnected = disc_tick is not None or sortie.ended_by_removal
    disc_death = fate == "disconnected" and disconnect_death(sortie, disc_tick if disc_tick is not None else end, rules)
    if fate == "disconnected" and not disc_death:
        loss = None  # FR-ING-21: a disconnect without recent damage is neither a death nor a loss
        died = None

    cutoff = loss.tick if loss is not None else end
    lost = loss is not None or died is not None or bailout or disc_death
    attacker_cause = lost and (shot_down_directly or attacker_involved(sortie, cutoff))
    loss_cause: LossCause = "none" if not lost else ("attacker" if attacker_cause else "self")
    structural = loss is not None and lost and structural_failure(sortie, loss, attacker_cause, rules)

    dead = died is not None or disc_death
    status_pos: Pos | None = None
    if not dead and not forced:
        if fate in ("bailed_out", "exited_on_ground"):
            status_pos = sortie.bot.removed_pos
        elif off and not airframe.airborne_at(end):
            status_pos = _landing_pos(sortie, end) or airframe.pos
    areas = list(facts.areas.values())
    captured = status_pos is not None and on_enemy_territory(status_pos, sortie.coalition, areas)

    damage_taken = min(1.0, sum(r.amount for r in airframe.damage_log if r.tick <= cutoff))
    bot_damage = any(r.tick <= cutoff for obj in unit_objects(sortie.bot) for r in obj.damage_log)
    pilot_status: PilotStatus = "dead" if dead else "captured" if captured else "wounded" if bot_damage else "healthy"
    aircraft_status: AircraftStatus = "destroyed" if loss is not None else "damaged" if damage_taken > 0 else "unharmed"

    outcome = _outcome(
        sortie, facts, end, lost=lost, attacker_cause=attacker_cause, off=off, forced=forced, fate=fate, final=final
    )
    return Verdict(
        end_tick=end,
        loss=loss,
        died_tick=died,
        disconnect_tick=disc_tick,
        took_off=off,
        flight_time_s=flight_time_s(sortie, end),
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
        structural_failure=structural,
        outcome=outcome,
        pilot_status=pilot_status,
        aircraft_status=aircraft_status,
        damage_taken=damage_taken,
        cutoff_tick=cutoff,
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
    fate: PilotFate,
    final: bool,
) -> Outcome:
    """Derived from il2_stats (MIT), see NOTICE: Sortie.sortie_status, extended for the Korea fates."""
    airframe = sortie.airframe
    if lost:
        return "shot_down" if attacker_cause else "crashed"
    if not off:
        return "not_taken_off"
    if forced:
        return "mission_ended"
    if sortie.is_open:
        return "in_flight" if airframe.airborne else "landed"
    airborne_at_end = sortie.airborne_at_end
    if fate == "disconnected":
        return "unknown" if airborne_at_end else _landed_or_ditched(sortie, facts, end)
    if airborne_at_end:
        return "in_flight"
    return _landed_or_ditched(sortie, facts, end)


def _landed_or_ditched(sortie: SortieState, facts: MissionFacts, end: int) -> Outcome:
    pos = _landing_pos(sortie, end)
    if pos is None:
        return "landed"
    friendly = [a for a in facts.airfields.values() if a.coalition == sortie.coalition]
    all_airfields = list(facts.airfields.values())
    return "landed" if at_friendly_airfield(pos, sortie.coalition, all_airfields) or not friendly else "ditched"
