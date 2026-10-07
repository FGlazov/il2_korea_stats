"""Resolve recorded facts into a `MissionResult`. Used by `snapshot()` (provisional) and `finish()` (final)."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from il2ks.core.replay.ammo import EMPTY_SORTIE_AMMO, AmmoAnalysis, analyse, merge_ammo_hits, rounds_fired
from il2ks.core.replay.attack import GroundTargets, combat_role, is_interception_victim, time_on_target_s
from il2ks.core.replay.balance import mission_balance, underdog_sorties
from il2ks.core.replay.breakdown import Breakdown, FriendlyFire, breakdowns, friendly_fire, timeline
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.fate import ticks, was_resupplied
from il2ks.core.replay.hits import hit_entries
from il2ks.core.replay.judge import Verdict, judge
from il2ks.core.replay.kills import first_blood_sortie, max_burst, resolve_kills
from il2ks.core.replay.model import MissionFacts, SortieState, is_bot_type
from il2ks.core.replay.pve import loss_class
from il2ks.core.replay.rams import ram_partners
from il2ks.core.replay.result import (
    AmmoCounts,
    CombatRole,
    KillResult,
    MissionInfo,
    MissionOutcome,
    MissionResult,
    SortieResult,
)


def mission_outcome(objectives: Mapping[int, bool]) -> tuple[MissionOutcome, int | None]:
    """The mission result from the AType 8 TYPE 0 reports (coalition -> completed): the one coalition that completed
    its objective won; both completing it or neither is a draw; no report at all is unknown (doc 12 "Mission
    result": 47 + 84 wins, 22 + 52 draws and 5 unknown over 210 missions)."""
    winners = [coalition for coalition, completed in sorted(objectives.items()) if completed]
    if len(winners) == 1:
        return "win", winners[0]
    return ("draw" if objectives else "unknown"), None


def _mission_info(facts: MissionFacts) -> MissionInfo:
    start = facts.start
    first_end = facts.first_mission_end
    result, winner = mission_outcome(facts.objectives)
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
        winning_coalition=winner,
        result=result,
    )


def _build_sortie(
    sortie: SortieState,
    verdict: Verdict,
    kills: list[KillResult],
    breakdown: Breakdown,
    friendly: FriendlyFire,
    rules: ReplayRules,
    targets: GroundTargets,
    ammo: AmmoAnalysis,
    roles: Mapping[int, CombatRole | None],
    first_blood: int | None,
    underdog: frozenset[int],
) -> SortieResult:
    role = combat_role(sortie)
    sortie_ammo = ammo.sorties.get(sortie.index, EMPTY_SORTIE_AMMO)
    mine = [k for k in kills if k.killer_sortie_index == sortie.index and not k.is_friendly]
    credited = [k for k in mine if k.credit == "kill"]
    ground = [k for k in credited if k.victim_kind == "ground"]
    air = [k for k in credited if k.victim_kind == "air"]
    assisted = [k for k in mine if k.credit == "assist"]
    assists_air = [k for k in assisted if k.victim_kind == "air"]
    assists_ground = [k for k in assisted if k.victim_kind == "ground"]
    airframe = sortie.airframe
    takeoffs = [t for t, _ in airframe.takeoffs if sortie.spawn_tick <= t <= verdict.active_end_tick]
    landings = [t for t, _ in airframe.landings if sortie.spawn_tick <= t <= verdict.active_end_tick]
    first_takeoff = takeoffs[0] if takeoffs else (sortie.spawn_tick if verdict.took_off else None)
    resupplied = was_resupplied(takeoffs, landings, rules)
    left_after_loss = verdict.loss is not None and verdict.end_tick - verdict.loss.tick > ticks(
        rules.ammo_left_after_loss_s
    )
    gun_hits = breakdown[2]
    ammo_left = sortie.ammo_left if isinstance(sortie.ammo_left, AmmoCounts) else None
    fired = (
        rounds_fired(
            sortie.ammo_loaded,
            ammo_left,
            resupplied=resupplied,
            left_after_loss=left_after_loss,
            gun_hits=gun_hits.air + gun_hits.ground,
        )
        if sortie.role == "pilot"
        else None
    )
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
        landing_damage=verdict.landing_damage,
        pilot_damage=verdict.pilot_damage,
        disconnected=verdict.disconnected,
        is_death=verdict.is_death,
        is_plane_lost=verdict.is_plane_lost,
        is_captured=verdict.is_captured,
        suspected_early_bailout=verdict.suspected_early_bailout,
        loss_cause=verdict.loss_cause,
        suspected_structural_failure=verdict.structural_failure,
        taxi_accident=verdict.taxi_accident,
        strafed_on_ground=verdict.strafed_on_ground,
        ended_by_mission_end=verdict.ended_by_mission_end,
        kills_air=len(air),
        kills_air_pvp=sum(1 for k in air if k.victim_sortie_index is not None),
        kills_air_ai=sum(1 for k in air if k.victim_sortie_index is None),
        kills_air_intercept=sum(
            1
            for k in air
            if is_interception_victim(
                k.victim_class, roles.get(k.victim_sortie_index) if k.victim_sortie_index is not None else None
            )
        ),
        loss_class=loss_class(sortie, verdict),
        rams=sum(1 for k in air if k.ram),
        first_blood=sortie.index == first_blood,
        multi_kill=max_burst([k.tick for k in air]),
        kills_ground=len(ground),
        kills_ground_by_category=dict(Counter(k.victim_ground_category or "other" for k in ground)),
        kills_ground_static=sum(1 for k in ground if k.victim_is_static),
        assists=len(assists_air) + len(assists_ground),
        assists_air=len(assists_air),
        assists_ground=len(assists_ground),
        ammo_loaded=sortie.ammo_loaded,
        ammo_left=ammo_left,
        rounds_fired=fired,
        gun_hits_air=gun_hits.air,
        gun_hits_ground=gun_hits.ground,
        ammo_hits=merge_ammo_hits(breakdown[1], sortie_ammo),
        ordnance=sortie_ammo.ordnance,
        store_releases=sortie_ammo.store_releases,
        rocket_salvos=sortie_ammo.rocket_salvos,
        ammo_unattributed=sortie_ammo.unattributed,
        underdog=sortie.index in underdog,
        damage=breakdown[0],
        timeline=timeline(sortie, verdict, kills, hit_entries(sortie_ammo.damage_events, rules)),
        friendly_kills=friendly.kills,
        friendly_hits=friendly.hits,
        friendly_damage=friendly.damage,
        resupplied=resupplied,
        ammo_left_after_loss=left_after_loss,
        combat_role=role,
        time_on_target_s=(
            time_on_target_s(sortie, targets, rules, active_end_tick=verdict.active_end_tick)
            if role == "attack"
            else None
        ),
    )


def resolve_mission(facts: MissionFacts, rules: ReplayRules, *, final: bool) -> MissionResult:
    facts.ram_partners = ram_partners(facts, rules)
    verdicts = [judge(sortie, facts, rules, final=final) for sortie in facts.sorties]
    kills = resolve_kills(facts, verdicts, rules)
    details = breakdowns(facts, verdicts)
    friendly = friendly_fire(facts, verdicts, kills)
    targets = GroundTargets(facts, rules.tot_target_radius_m)
    ammo = analyse(facts, verdicts, kills, rules)
    roles: dict[int, CombatRole | None] = {sortie.index: combat_role(sortie) for sortie in facts.sorties}
    first_blood = first_blood_sortie(kills, frozenset(s.index for s in facts.sorties if s.role == "pilot"))
    end_ticks = {v_sortie.index: verdict.end_tick for v_sortie, verdict in zip(facts.sorties, verdicts, strict=True)}
    underdog = underdog_sorties(facts.sorties, end_ticks)
    sorties = tuple(
        _build_sortie(
            sortie,
            verdict,
            kills,
            details[sortie.index],
            friendly[sortie.index],
            rules,
            targets,
            ammo,
            roles,
            first_blood,
            underdog,
        )
        for sortie, verdict in zip(facts.sorties, verdicts, strict=True)
    )
    seen = frozenset(t for t in facts.types_seen if not is_bot_type(t))
    unknown = frozenset(t for t, known in facts.types_seen.items() if not known and not is_bot_type(t))
    return MissionResult(
        mission=_with_balance(_mission_info(facts), facts, end_ticks),
        sorties=sorties,
        kills=tuple(kills),
        object_types_seen=seen,
        unknown_object_types=unknown,
        single_attacker_kills=ammo.single_attacker_kills,
    )


def _with_balance(info: MissionInfo, facts: MissionFacts, end_ticks: Mapping[int, int]) -> MissionInfo:
    """`info` with the time-weighted pilots per side (OQ-134)."""
    balance = mission_balance(facts.sorties, end_ticks, info.end_tick)
    return replace(info, redfor_players=balance.redfor_players, blufor_players=balance.blufor_players)
