"""Resolve recorded facts into a `MissionResult`. Used by `snapshot()` (provisional) and `finish()` (final)."""

from il2ks.core.replay.breakdown import FriendlyFire, breakdowns, friendly_fire, timeline
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.fate import was_resupplied
from il2ks.core.replay.judge import Verdict, judge
from il2ks.core.replay.kills import resolve_kills
from il2ks.core.replay.model import MissionFacts, SortieState, is_bot_type
from il2ks.core.replay.result import (
    AmmoCounts,
    AmmoHits,
    DamageExchange,
    KillResult,
    MissionInfo,
    MissionResult,
    SortieResult,
)


def _mission_info(facts: MissionFacts) -> MissionInfo:
    start = facts.start
    first_end = facts.first_mission_end
    return MissionInfo(
        mission_file=start.mission_file if start else "",
        game_date=start.game_date if start else "",
        game_time=start.game_time if start else "",
        game_type=start.game_type if start else 0,
        settings=start.settings if start else "",
        countries=dict(facts.countries),
        log_version=facts.log_version,
        end_tick=first_end if first_end is not None else facts.last_tick,
        last_tick=facts.last_tick,
        completed_cleanly=first_end is not None,
        winning_coalition=facts.winner,
    )


def _build_sortie(
    sortie: SortieState,
    verdict: Verdict,
    kills: list[KillResult],
    breakdown: tuple[tuple[DamageExchange, ...], tuple[AmmoHits, ...]],
    friendly: FriendlyFire,
    rules: ReplayRules,
) -> SortieResult:
    mine = [k for k in kills if k.killer_sortie_index == sortie.index and not k.is_friendly]
    credited = [k for k in mine if k.credit == "kill"]
    airframe = sortie.airframe
    takeoffs = [t for t, _ in airframe.takeoffs if sortie.spawn_tick <= t <= verdict.active_end_tick]
    landings = [t for t, _ in airframe.landings if sortie.spawn_tick <= t <= verdict.active_end_tick]
    first_takeoff = takeoffs[0] if takeoffs else (sortie.spawn_tick if verdict.took_off else None)
    return SortieResult(
        index=sortie.index,
        account_uuid=sortie.account_uuid,
        profile_uuid=sortie.profile_uuid,
        name=sortie.name,
        aircraft_type=sortie.aircraft_type,
        aircraft_id=sortie.aircraft_id,
        bot_id=sortie.bot_id,
        country=sortie.country,
        coalition=sortie.coalition,
        role=sortie.role,
        parent_sortie_index=sortie.parent_sortie.index if sortie.parent_sortie is not None else None,
        spawn_tick=sortie.spawn_tick,
        spawn_type=sortie.spawn_type,
        spawn_pos=sortie.spawn_pos,
        payload_id=sortie.payload_id,
        weapon_mods=sortie.weapon_mods,
        fuel=sortie.fuel,
        skin=sortie.skin,
        takeoff_tick=first_takeoff,
        landing_tick=landings[-1] if landings else None,
        end_tick=verdict.end_tick,
        flight_time_s=verdict.flight_time_s,
        takeoffs=len(takeoffs),
        landings=len(landings),
        outcome=verdict.outcome,
        pilot_fate=verdict.fate,
        pilot_fate_source=verdict.fate_source,
        pilot_status=verdict.pilot_status,
        aircraft_status=verdict.aircraft_status,
        damage_taken=verdict.damage_taken,
        disconnected=verdict.disconnected,
        is_death=verdict.is_death,
        is_plane_lost=verdict.is_plane_lost,
        is_captured=verdict.is_captured,
        suspected_early_bailout=verdict.suspected_early_bailout,
        loss_cause=verdict.loss_cause,
        suspected_structural_failure=verdict.structural_failure,
        kills_air=sum(1 for k in credited if k.victim_kind == "air"),
        kills_ground=sum(1 for k in credited if k.victim_kind == "ground"),
        assists=sum(1 for k in mine if k.credit == "assist"),
        ammo_loaded=sortie.ammo_loaded,
        ammo_left=sortie.ammo_left if isinstance(sortie.ammo_left, AmmoCounts) else None,
        ammo_hits=breakdown[1],
        damage=breakdown[0],
        timeline=timeline(sortie, verdict, kills),
        friendly_kills=friendly.kills,
        friendly_hits=friendly.hits,
        friendly_damage=friendly.damage,
        resupplied=was_resupplied(takeoffs, landings, rules),
    )


def resolve_mission(facts: MissionFacts, rules: ReplayRules, *, final: bool) -> MissionResult:
    verdicts = [judge(sortie, facts, rules, final=final) for sortie in facts.sorties]
    kills = resolve_kills(facts, verdicts, rules)
    details = breakdowns(facts, verdicts)
    friendly = friendly_fire(facts, verdicts, kills)
    sorties = tuple(
        _build_sortie(sortie, verdict, kills, details[sortie.index], friendly[sortie.index], rules)
        for sortie, verdict in zip(facts.sorties, verdicts, strict=True)
    )
    seen = frozenset(t for t in facts.types_seen if not is_bot_type(t))
    unknown = frozenset(t for t, known in facts.types_seen.items() if not known and not is_bot_type(t))
    return MissionResult(
        mission=_mission_info(facts),
        sorties=sorties,
        kills=tuple(kills),
        object_types_seen=seen,
        unknown_object_types=unknown,
    )
