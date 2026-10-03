"""Builders for replay results and a fake catalog, reusable by any test.

Every builder has sensible defaults for one plain sortie that took off and landed; pass keyword arguments for the fields
a test cares about, or `dataclasses.replace` for anything else.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from django.db import models, transaction

from il2ks.core.catalog.loader import AIR_CLASSES, Catalog, ObjectClass, ObjectInfo, PayloadInfo
from il2ks.core.logparse.events import AccountUuid, ObjectId, Pos, ProfileUuid
from il2ks.core.replay.result import (
    AmmoCounts,
    KillCredit,
    KillResult,
    MissionInfo,
    MissionResult,
    Outcome,
    PilotFate,
    Role,
    SortieResult,
    TargetKind,
)
from il2ks.db.models import Mission
from il2ks.ingest.persist import MissionMeta, save_mission

SERVER_UID = uuid.UUID("00000000-0000-4000-8000-000000000001")
STARTED_AT = datetime(2026, 9, 19, 20, 34, 13, tzinfo=UTC)

KNOWN_OBJECTS: dict[str, tuple[str, ObjectClass]] = {
    "MiG-15bis": ("MiG-15bis", "fighter"),
    "F-86A-5": ("F-86A Sabre", "fighter"),
    "F-51D": ("P-51D Mustang", "fighter"),
    "Il-10": ("Il-10", "attacker"),
    "Turret_IL10": ("Il-10 turret", "gunner"),
    "M46 Patton": ("M46 Patton", "tank"),
}


def account(n: int) -> AccountUuid:
    return AccountUuid(f"00000000-0000-4000-8000-{n:012d}")


def profile(n: int) -> ProfileUuid:
    return ProfileUuid(f"10000000-0000-4000-8000-{n:012d}")


class FakeCatalog(Catalog):
    """A small in-memory catalog: `KNOWN_OBJECTS` are known, everything else is unknown."""

    def __init__(self, objects: dict[str, tuple[str, ObjectClass]] | None = None) -> None:
        self.objects = KNOWN_OBJECTS if objects is None else objects

    def lookup(self, object_type: str) -> ObjectInfo:
        known = self.objects.get(object_type)
        if known is None:
            return ObjectInfo(object_type, object_type, "unknown", is_playable=False, is_known=False)
        name, cls = known
        return ObjectInfo(object_type, name, cls, is_playable=cls in AIR_CLASSES, is_known=True)

    def payload(self, aircraft_type: str, payload_id: int) -> PayloadInfo | None:
        if payload_id < 0 or aircraft_type not in self.objects:
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
    kills_ground: int = 0,
    assists: int = 0,
    is_death: bool = False,
    is_plane_lost: bool = False,
    flight_time_s: float = 600.0,
    damage_taken: float = 0.0,
    payload_id: int = 1,
) -> SortieResult:
    """One sortie by player number `player` (account `account(player)`)."""
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
        country=501 if coalition == 1 else 601,
        coalition=coalition,
        role=role,
        parent_sortie_index=None,
        spawn_tick=spawn,
        spawn_type="parking",
        spawn_pos=Pos(100.0, 50.0, 200.0),
        payload_id=payload_id,
        weapon_mods=0,
        fuel=1.0,
        skin="",
        takeoff_tick=took_off,
        landing_tick=end - 250 if outcome == "landed" else None,
        end_tick=end,
        flight_time_s=flight_time_s,
        takeoffs=1,
        landings=1 if outcome == "landed" else 0,
        outcome=outcome,
        pilot_fate=pilot_fate,
        pilot_fate_source="event" if pilot_fate != "unknown" else "unknown",
        pilot_status="dead" if is_death else "healthy",
        aircraft_status="destroyed" if is_plane_lost else "unharmed",
        damage_taken=damage_taken,
        disconnected=False,
        is_death=is_death,
        is_plane_lost=is_plane_lost,
        is_captured=False,
        suspected_early_bailout=False,
        loss_cause="attacker" if is_plane_lost else "none",
        suspected_structural_failure=False,
        kills_air=kills_air,
        kills_ground=kills_ground,
        assists=assists,
        ammo_loaded=AmmoCounts(bullets=400),
        ammo_left=AmmoCounts(bullets=200),
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
            countries={501: 1, 601: 2},
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


def save(result: MissionResult, mission_meta: MissionMeta | None = None, catalog: Catalog | None = None) -> Mission:
    """`save_mission` inside a transaction, the way the ingest runner calls it."""
    with transaction.atomic():
        return save_mission(result, meta() if mission_meta is None else mission_meta, catalog or FakeCatalog())


def rows(model: type[models.Model]) -> list[dict[str, object]]:
    """All rows of a table as plain dicts (PK included), ordered by PK: for comparing DB states."""
    return list(model._default_manager.order_by("pk").values())
