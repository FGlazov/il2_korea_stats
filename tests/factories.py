"""Builders for replay results and a fake catalog, reusable by any test.

Every builder has sensible defaults for one plain sortie that took off and landed; pass keyword arguments for the fields
a test cares about, or `dataclasses.replace` for anything else.
"""

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

from django.db import models, transaction

from il2ks.core.catalog.loader import (
    AIR_CLASSES,
    Catalog,
    GroundCategory,
    ObjectClass,
    ObjectInfo,
    PayloadInfo,
    Propulsion,
)
from il2ks.core.logparse.events import AccountUuid, ObjectId, Pos, ProfileUuid
from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules
from il2ks.core.replay.result import (
    AmmoCounts,
    CombatRole,
    KillCredit,
    KillResult,
    LossClass,
    MissionInfo,
    MissionResult,
    Outcome,
    PilotFate,
    Role,
    SortieResult,
    TargetKind,
)
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.db.models import Mission
from il2ks.ingest.persist import MissionMeta, save_mission

SERVER_UID = uuid.UUID("00000000-0000-4000-8000-000000000001")
STARTED_AT = datetime(2026, 9, 19, 20, 34, 13, tzinfo=UTC)

KNOWN_OBJECTS: dict[str, tuple[str, ObjectClass]] = {
    "MiG-15bis": ("MiG-15bis", "fighter"),
    "F-86A-5": ("F-86A Sabre", "fighter"),
    "F-51D": ("P-51D Mustang", "fighter"),
    "IL-10": ("IL-10", "attacker"),
    "Turret_IL10": ("Il-10 turret", "gunner"),
    "M46 Patton": ("M46 Patton", "tank"),
    "GAZ_63": ("GAZ-63", "static"),
    "Fence wire 5m": ("Fence wire 5m", "static"),
}

GROUND_CATEGORIES: dict[str, GroundCategory] = {"M46 Patton": "tank", "GAZ_63": "vehicle", "Fence wire 5m": "other"}


JETS = frozenset({"MiG-15bis", "F-86A-5"})
"""Which of `KNOWN_OBJECTS` are jets; the other aircraft are propeller-driven, gunners and ground objects have none."""


def account(n: int) -> AccountUuid:
    return AccountUuid(f"00000000-0000-4000-8000-{n:012d}")


def profile(n: int) -> ProfileUuid:
    return ProfileUuid(f"10000000-0000-4000-8000-{n:012d}")


class FakeCatalog(Catalog):
    """A small in-memory catalog: `KNOWN_OBJECTS` are known, everything else is unknown."""

    def __init__(self, objects: dict[str, tuple[str, ObjectClass]] | None = None) -> None:
        self.known_objects = KNOWN_OBJECTS if objects is None else objects

    def lookup(self, object_type: str) -> ObjectInfo:
        known = self.known_objects.get(object_type)
        if known is None:
            return ObjectInfo(object_type, object_type, "unknown", is_playable=False, is_known=False)
        name, cls = known
        propulsion: Propulsion | None = None
        if cls in ("fighter", "attacker", "bomber", "transport"):
            propulsion = "jet" if object_type in JETS else "prop"
        return ObjectInfo(
            object_type,
            name,
            cls,
            is_playable=cls in AIR_CLASSES,
            is_known=True,
            propulsion=propulsion,
            ground_category=GROUND_CATEGORIES.get(object_type),
        )

    def payload(self, aircraft_type: str, payload_id: int) -> PayloadInfo | None:
        if payload_id < 0 or aircraft_type not in self.known_objects:
            return None
        return PayloadInfo(aircraft_type.lower(), payload_id, f"P{payload_id}", f"Payload {payload_id}")

    def coalition_name(self, coalition: int) -> str:
        return {1: "REDFOR", 2: "BLUFOR"}.get(coalition, "Neutral")


def meta(mission_uid: str = "2026-09-19_22-34-13", started_at: datetime = STARTED_AT) -> MissionMeta:
    return MissionMeta(
        server_uid=SERVER_UID,
        mission_uid=mission_uid,
        started_at=started_at,
        archive_path=f"archive/{mission_uid}.zip",
    )


def sortie(
    index: int,
    player: int,
    *,
    name: str | None = None,
    aircraft_type: str = "MiG-15bis",
    coalition: int = 1,
    role: Role = "pilot",
    spawn_tick: int | None = None,
    end_tick: int | None = None,
    outcome: Outcome = "landed",
    pilot_fate: PilotFate = "in_aircraft",
    kills_air: int = 0,
    kills_air_pvp: int = 0,
    kills_air_ai: int | None = None,
    kills_air_intercept: int = 0,
    kills_ground: int = 0,
    ground_by_category: Mapping[GroundCategory, int] | None = None,
    kills_ground_static: int = 0,
    assists: int = 0,  # air assists (the legacy name); `assists_ground` are on top
    assists_ground: int = 0,
    is_death: bool = False,
    is_plane_lost: bool = False,
    loss_class: LossClass | None = None,
    flight_time_s: float = 600.0,
    damage_taken: float = 0.0,
    pilot_damage: float | None = None,
    payload_id: int = 1,
    weapon_mods: int = 0,
    country: int | None = None,
    friendly_kills: int = 0,
    friendly_hits: int = 0,
    friendly_damage: float = 0.0,
    resupplied: bool = False,
    ammo_left_after_loss: bool = False,
    rounds_fired: int | None = 200,  # = loaded 400 - left 200 of the default ammo (pilots); None = unknown
    gun_hits_air: int = 0,
    gun_hits_ground: int = 0,
    store_releases: int = 0,
    rocket_salvos: int = 0,
    taxi_accident: bool = False,
    strafed_on_ground: bool = False,
    ended_by_mission_end: bool = False,
    combat_role: CombatRole | None = None,
    time_on_target_s: float | None = None,
    rams: int = 0,
    first_blood: bool = False,
    multi_kill: int = 0,
    ammo_loaded: AmmoCounts = AmmoCounts(bullets=400),  # noqa: B008 - frozen dataclass
    ammo_left: AmmoCounts | None = AmmoCounts(bullets=200),  # noqa: B008
) -> SortieResult:
    """One sortie by player number `player` (account `account(player)`).

    Ground kills: `ground_by_category` sets the breakdown (and `kills_ground` becomes its sum); without it, all
    `kills_ground` kills are of category "other", so the breakdown always sums to `kills_ground`.

    Air kills: `kills_air_pvp` of them are of player aircraft, the rest AI (`kills_air_ai` given: `kills_air` becomes
    pvp + ai). A lost sortie (or a death) is lost to a "player" unless `loss_class` says otherwise."""
    if ground_by_category is None:
        ground_by_category = {"other": kills_ground} if kills_ground else {}
    kills_ground = sum(ground_by_category.values())
    if kills_air_ai is None:
        kills_air_ai = kills_air - kills_air_pvp
    kills_air = kills_air_pvp + kills_air_ai
    if loss_class is None and (is_plane_lost or is_death):
        loss_class = "player"
    spawn = 1000 * (index + 1) if spawn_tick is None else spawn_tick
    took_off = spawn + 500
    end = took_off + int(flight_time_s * 50) + 500 if end_tick is None else end_tick
    return SortieResult(
        index=index,
        account_uuid=account(player),
        profile_uuid=profile(player),
        name=f"Player-{player}" if name is None else name,
        aircraft_type=aircraft_type,
        aircraft_id=ObjectId(10_000 + index),
        bot_id=ObjectId(20_000 + index),
        country=country if country is not None else (501 if coalition == 1 else 601),
        coalition=coalition,
        role=role,
        parent_sortie_index=None,
        spawn_tick=spawn,
        spawn_type="parking",
        spawn_pos=Pos(100.0, 50.0, 200.0),
        payload_id=payload_id,
        weapon_mods=weapon_mods,
        fuel=1.0,
        skin="",
        takeoff_tick=took_off,
        landing_tick=end - 250 if outcome == "landed" else None,
        end_tick=end,
        flight_time_s=flight_time_s,
        takeoffs=0 if outcome == "not_taken_off" else 1,
        landings=1 if outcome == "landed" else 0,
        outcome=outcome,
        pilot_fate=pilot_fate,
        pilot_fate_source="event" if pilot_fate != "unknown" else "unknown",
        pilot_status="dead" if is_death else "healthy",
        aircraft_status="destroyed" if is_plane_lost else "unharmed",
        damage_taken=damage_taken,
        pilot_damage=(1.0 if is_death else 0.0) if pilot_damage is None else pilot_damage,
        disconnected=False,
        is_death=is_death,
        is_plane_lost=is_plane_lost,
        is_captured=False,
        suspected_early_bailout=False,
        loss_cause="attacker" if is_plane_lost else "none",
        suspected_structural_failure=False,
        kills_air=kills_air,
        kills_air_pvp=kills_air_pvp,
        kills_air_ai=kills_air_ai,
        kills_air_intercept=kills_air_intercept,
        loss_class=loss_class,
        kills_ground=kills_ground,
        kills_ground_by_category=ground_by_category,
        kills_ground_static=kills_ground_static,
        assists=assists + assists_ground,
        assists_air=assists,
        assists_ground=assists_ground,
        ammo_loaded=ammo_loaded,
        ammo_left=ammo_left,
        rounds_fired=rounds_fired if role == "pilot" else None,
        gun_hits_air=gun_hits_air,
        gun_hits_ground=gun_hits_ground,
        friendly_kills=friendly_kills,
        friendly_hits=friendly_hits,
        friendly_damage=friendly_damage,
        resupplied=resupplied,
        ammo_left_after_loss=ammo_left_after_loss,
        store_releases=store_releases,
        rocket_salvos=rocket_salvos,
        taxi_accident=taxi_accident,
        strafed_on_ground=strafed_on_ground,
        ended_by_mission_end=ended_by_mission_end,
        combat_role=combat_role,
        time_on_target_s=time_on_target_s,
        rams=rams,
        first_blood=first_blood,
        multi_kill=multi_kill,
    )


def kill(
    tick: int,
    killer: int | None,
    victim: int | None,
    *,
    victim_type: str = "F-86A-5",
    victim_kind: TargetKind = "air",
    killer_type: str | None = "MiG-15bis",
    credit: KillCredit = "kill",
    is_friendly: bool = False,
) -> KillResult:
    """A kill credit; `killer` / `victim` are sortie indexes (None = AI or ground object)."""
    return KillResult(
        tick=tick,
        victim_object_id=ObjectId(30_000 + tick),
        victim_type=victim_type,
        victim_kind=victim_kind,
        victim_coalition=None,
        victim_sortie_index=victim,
        killer_sortie_index=killer,
        killer_type=killer_type,
        credit=credit,
        via="direct",
        is_friendly=is_friendly,
        pos=Pos(1.0, 2.0, 3.0),
    )


def mission(
    sorties: tuple[SortieResult, ...] = (),
    kills: tuple[KillResult, ...] = (),
    *,
    end_tick: int | None = None,
    extra_types: frozenset[str] = frozenset(),
    countries: dict[int, int] | None = None,
) -> MissionResult:
    """A mission holding `sorties` (their `index` must match their position) and `kills`."""
    assert [s.index for s in sorties] == list(range(len(sorties))), "sortie.index must equal its position"
    end = max((s.end_tick for s in sorties), default=0) + 100 if end_tick is None else end_tick
    types = frozenset(s.aircraft_type for s in sorties) | extra_types
    return MissionResult(
        mission=MissionInfo(
            mission_file="missions/korea_test",
            game_date="1951.9.15",
            game_time="13:0:0",
            game_type=2,
            settings="0010001",
            countries={501: 1, 601: 2} if countries is None else countries,
            log_version=None,
            end_tick=end,
            last_tick=end,
            completed_cleanly=True,
            winning_coalition=None,
        ),
        sorties=sorties,
        kills=kills,
        object_types_seen=types,
        unknown_object_types=frozenset(),
    )


def reindexed(sorties: tuple[SortieResult, ...]) -> tuple[SortieResult, ...]:
    """Renumber sortie indexes to their positions (after removing one from a tuple)."""
    return tuple(replace(s, index=i) for i, s in enumerate(sorties))


def save(
    result: MissionResult,
    mission_meta: MissionMeta | None = None,
    catalog: Catalog | None = None,
    ratings: RatingRules | None = DEFAULT_RULES,
    marks: MarkRules | None = DEFAULT_MARK_RULES,
    score: ScoreRules = DEFAULT_SCORE_RULES,
) -> Mission:
    """`save_mission` inside a transaction, the way the ingest runner calls it (`ratings=None`: no Elo replay)."""
    with transaction.atomic():
        return save_mission(
            result,
            meta() if mission_meta is None else mission_meta,
            catalog or FakeCatalog(),
            ratings,
            marks=marks,
            score=score,
        )


def rows(model: type[models.Model]) -> list[dict[str, object]]:
    """All rows of a table as plain dicts (PK included), ordered by PK: for comparing DB states."""
    return list(model._default_manager.order_by("pk").values())
