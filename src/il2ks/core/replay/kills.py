"""Kills and assists: one `KillResult` per credit with a player on at least one side (TD-21, FR-ING-22).

Derived from il2_stats (MIT), see NOTICE: `Object.got_killed`. Credit is damage based; explicit AID wins the kill.
"""

from dataclasses import dataclass

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.credit import Credited, credit_kill, is_self_attack
from il2ks.core.replay.judge import Verdict
from il2ks.core.replay.model import MissionFacts, SortieState, TrackedObject
from il2ks.core.replay.result import KillCredit, KillResult, KillVia


@dataclass(frozen=True, slots=True)
class _Victim:
    obj: TrackedObject
    sortie: SortieState | None
    tick: int
    pos: Pos | None
    explicit: TrackedObject | None
    via: KillVia


def _killer_type(credited: Credited) -> str:
    party = credited.party
    return party.aircraft_type if isinstance(party, SortieState) else party.object_type


def _party_sortie_index(credited: Credited) -> int | None:
    return credited.party.index if isinstance(credited.party, SortieState) else None


def _party_coalition(credited: Credited) -> int | None:
    party = credited.party
    return party.coalition


def _is_friendly(credited: Credited, victim_coalition: int | None) -> bool:
    coalition = _party_coalition(credited)
    return coalition is not None and coalition == victim_coalition and coalition != 0


def _is_victim_object(obj: TrackedObject) -> bool:
    """Crew deaths aren't kills; neither are bombs and drop tanks (ordnance), parachutes, ejection seats, spotters and
    vehicle turrets (catalog classes `crew` and `equipment`), or undeclared placeholders."""
    if obj.is_bot or obj.object_type == "":
        return False
    return obj.info.cls not in ("ordnance", "gunner", "crew", "equipment")


def _via(sortie: SortieState, verdict: Verdict, explicit: TrackedObject | None) -> KillVia:
    if explicit is not None and not is_self_attack(explicit, sortie.airframe, sortie):
        return "direct"
    if verdict.fate == "disconnected":
        return "disconnect"
    if verdict.fate in ("bailed_out", "exited_on_ground"):
        return "abandoned_aircraft"
    return "direct"


def _victims(facts: MissionFacts, verdicts: list[Verdict], rules: ReplayRules) -> list[_Victim]:
    victims: list[_Victim] = []
    in_sortie: set[int] = set()
    for sortie, verdict in zip(facts.sorties, verdicts, strict=True):
        airframe = sortie.airframe
        if sortie.role == "gunner":
            continue  # a gunner's death isn't a kill of the aircraft; the pilot's sortie carries it
        in_sortie.add(id(airframe))

        loss = verdict.loss
        if loss is not None:
            victims.append(_Victim(airframe, sortie, loss.tick, loss.pos, loss.by, _via(sortie, verdict, loss.by)))
        elif verdict.is_plane_lost and (verdict.disconnect_death or verdict.bailout or verdict.died_tick is not None):
            # Lost without an AType 3 for the aircraft: pilot killed, abandoned or disconnected aircraft
            bot = sortie.bot
            explicit = bot.destroyed_by if verdict.died_tick is not None else None
            tick = verdict.died_tick if verdict.died_tick is not None else verdict.end_tick
            pos = bot.destroyed_pos if verdict.died_tick is not None else bot.removed_pos
            victims.append(
                _Victim(airframe, sortie, tick, pos or airframe.pos, explicit, _via(sortie, verdict, explicit))
            )
    for obj in facts.destroyed:
        if id(obj) in in_sortie or not _is_victim_object(obj) or obj.destroyed_tick is None:
            continue
        sortie = obj.sortie
        if sortie is not None:  # a player's aircraft that isn't a victim of its own sortie (late destruction, gunner)
            continue
        victims.append(_Victim(obj, None, obj.destroyed_tick, obj.destroyed_pos, obj.destroyed_by, "direct"))
    return victims


def _gunner_kill_pilot(entry: Credited, *, friendly: bool) -> SortieState | None:
    """The player pilot whose gunner got a (non-friendly) kill shares it as an assist `[PROPOSED]`
    (design_doc/13_game_rules.md, Kills and credit).

    Harmless but dormant on real data: the sample logs never name a turret or gunner bot as the attacker (AID); gunner
    fire is credited to the parent aircraft, so the pilot already gets those kills directly (research, 210 missions)."""
    party = entry.party
    if not entry.is_killer or friendly or not isinstance(party, SortieState) or party.role != "gunner":
        return None
    return party.parent_sortie


def _result(
    victim: _Victim,
    *,
    killer_index: int | None,
    killer_type: str | None,
    killer_coalition: int | None,
    credit: KillCredit,
    via: KillVia,
    friendly: bool,
) -> KillResult:
    info = victim.obj.info
    is_ground = not info.is_air
    return KillResult(
        victim_ground_category=(info.ground_category or "other") if is_ground else None,
        victim_is_static=is_ground and info.is_static,
        tick=victim.tick,
        victim_object_id=victim.obj.object_id,
        victim_type=victim.obj.object_type,
        victim_kind="air" if victim.obj.info.is_air else "ground",
        victim_coalition=victim.obj.coalition,
        victim_sortie_index=victim.sortie.index if victim.sortie is not None else None,
        killer_sortie_index=killer_index,
        killer_type=killer_type,
        killer_coalition=killer_coalition,
        credit=credit,
        via=via,
        is_friendly=friendly,
        pos=victim.pos,
    )


def resolve_kills(facts: MissionFacts, verdicts: list[Verdict], rules: ReplayRules) -> list[KillResult]:
    """Every kill and assist where a player is the victim or a credited party. Sorted by tick (stable).

    At most one result per (victim, killer sortie): a gunner's pilot gets the extra assist only if the pilot isn't
    credited for that victim already (design_doc/13_game_rules.md, Kills and credit)."""
    results: list[KillResult] = []
    for victim in _victims(facts, verdicts, rules):
        credited = credit_kill(victim.obj, victim.sortie, victim.explicit, victim.tick, rules.assist_min_damage)
        victim_index = victim.sortie.index if victim.sortie is not None else None
        if not credited:
            if victim_index is not None:  # environment or self: the victim's death without credit
                results.append(
                    _result(
                        victim,
                        killer_index=None,
                        killer_type=None,
                        killer_coalition=None,
                        credit="kill",
                        via=victim.via,
                        friendly=False,
                    )
                )
            continue
        victim_coalition = victim.obj.coalition
        for entry in credited:
            killer_index = _party_sortie_index(entry)
            if killer_index is None and victim_index is None:
                continue  # AI against AI
            friendly = _is_friendly(entry, victim_coalition)
            results.append(
                _result(
                    victim,
                    killer_index=killer_index,
                    killer_type=_killer_type(entry),
                    killer_coalition=_party_coalition(entry),
                    credit="kill" if entry.is_killer else "assist",
                    via=victim.via if victim_index is not None else "direct",
                    friendly=friendly,
                )
            )
        taken = {index for index in map(_party_sortie_index, credited) if index is not None}
        for entry in credited:
            friendly = _is_friendly(entry, victim_coalition)
            pilot = _gunner_kill_pilot(entry, friendly=friendly)
            if pilot is not None and pilot.index not in taken:
                taken.add(pilot.index)
                results.append(
                    _result(
                        victim,
                        killer_index=pilot.index,
                        killer_type=pilot.aircraft_type,
                        killer_coalition=pilot.coalition,
                        credit="assist",
                        via="direct",
                        friendly=False,
                    )
                )
    results.sort(key=lambda k: k.tick)
    return results
