"""Scenario builder for replay tests: write a mission as a few readable calls, get typed events back.

Times are in seconds from mission start (converted to ticks). Add events in time order. Ids used by the scenarios:
player A (acct 1): aircraft 100, bot 101, F-86A-5 of country 601 (coalition 2)
player B (acct 2): aircraft 200, bot 201, MiG-15bis of country 501 (coalition 1)
player C (acct 3): aircraft 500, bot 501, MiG-15bis of country 501
AI aircraft 300 (MiG-15bis, 501), AI tanks 400/401 (M46 Patton, 601 / 501).
"""

from types import MappingProxyType

from il2ks.core.catalog.loader import Catalog, GroundCategory, ObjectClass, ObjectInfo
from il2ks.core.logparse.events import (
    NO_OBJECT,
    AccountUuid,
    AirfieldEvent,
    AreaBoundaryEvent,
    BailoutEvent,
    BotRemovedEvent,
    DamageEvent,
    HitEvent,
    InfluenceAreaEvent,
    KillEvent,
    LandingEvent,
    LogEvent,
    MissionEndEvent,
    MissionStartEvent,
    ObjectId,
    ObjectSpawnEvent,
    PlayerDisconnectEvent,
    PlayerSpawnEvent,
    Pos,
    ProfileUuid,
    SortieEndEvent,
    TakeoffEvent,
    WheelsOffEvent,
    WheelsOnEvent,
)
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult, SortieResult
from il2ks.core.replay.state import run

OBJECTS: dict[str, ObjectClass] = {
    "F-86A-5": "fighter",
    "MiG-15bis": "fighter",
    "IL-10": "attacker",
    "Turret_IL10": "gunner",
    "M46 Patton": "tank",
    "Flak 37": "aaa",
    "B-29": "bomber",
    "C-47B": "transport",
    "Li-2": "transport",
    "GAZ_63": "static",  # a static truck: category vehicle, but static
    "Military tent A2": "static",
    "Cargo ship 1": "ship",
    "Uncategorised static": "static",  # a ground object the catalog gave no category: shown as "other"
    "BotPlanePilot_Test": "crew",
    "BotGunner_Test": "crew",
    "CParachute": "equipment",
    "ESeat_MiG-15bis": "equipment",
    "VehicleTurret": "equipment",
}
GROUND_CATEGORIES: dict[str, GroundCategory] = {
    "M46 Patton": "tank",
    "Flak 37": "aaa",
    "GAZ_63": "vehicle",
    "Military tent A2": "building",
    "Cargo ship 1": "ship",
}
FAR = Pos(10_000.0, 3_000.0, 10_000.0)
GROUND = Pos(2_000.0, 100.0, 2_000.0)


class FakeCatalog(Catalog):
    """Knows `OBJECTS` only; anything else is unknown."""

    def lookup(self, object_type: str) -> ObjectInfo:
        cls = OBJECTS.get(object_type)
        if cls is None:
            return ObjectInfo(object_type, object_type, "unknown", is_playable=False, is_known=False)
        return ObjectInfo(
            object_type,
            object_type,
            cls,
            is_playable=cls == "fighter",
            is_known=True,
            ground_category=GROUND_CATEGORIES.get(object_type),
        )


def ids(n: int) -> ObjectId:
    return ObjectId(n)


def tick(seconds: float) -> int:
    return round(seconds * 50)


class Scenario:
    def __init__(self) -> None:
        self.events: list[LogEvent] = []
        self.add(
            MissionStartEvent(
                tick=0,
                game_date="1951.9.15",
                game_time="13:0:0",
                mission_file="Missions/test.msnbin",
                mission_id="",
                game_type=2,
                countries=MappingProxyType({0: 0, 501: 1, 502: 1, 601: 2, 602: 2}),
                settings="0",
                mods=0,
                preset=0,
                aqm_id=0,
            )
        )

    def add(self, event: LogEvent) -> None:
        self.events.append(event)

    # --- objects and sorties -----------------------------------------------------------------------------------------

    def declare(
        self, t: float, oid: int, object_type: str, country: int, *, parent: int = -1, pos: Pos = GROUND
    ) -> None:
        self.add(
            ObjectSpawnEvent(
                tick=tick(t),
                object_id=ObjectId(oid),
                object_type=object_type,
                country=country,
                name=object_type,
                parent_id=ObjectId(parent),
                pos=pos,
            )
        )

    def player(
        self,
        t: float,
        aircraft: int,
        bot: int,
        acct: int,
        *,
        aircraft_type: str = "F-86A-5",
        country: int = 601,
        in_air: int = 2,
        parent: int = -1,
        pos: Pos = GROUND,
        name: str = "Pilot",
    ) -> None:
        """AType 12 for the aircraft and the pilot, AType 10, and wheels-on (as at a real spawn)."""
        self.declare(t, aircraft, aircraft_type, country, parent=parent, pos=pos)
        self.declare(t, bot, "BotPlanePilot_Test", country, parent=aircraft, pos=pos)
        self.add(
            PlayerSpawnEvent(
                tick=tick(t),
                aircraft_id=ObjectId(aircraft),
                bot_id=ObjectId(bot),
                bullets=100,
                shells=50,
                bombs=0,
                rockets=4,
                pos=pos,
                profile_uuid=ProfileUuid(f"profile-{acct}"),
                account_uuid=AccountUuid(f"account-{acct}"),
                name=f"{name}{acct}",
                aircraft_type=aircraft_type,
                country=country,
                form=0,
                airfield_id=ObjectId(1),
                in_air=in_air,
                parent_id=ObjectId(parent),
                is_player=True,
                is_tstart=False,
                payload_id=0,
                fuel=1.0,
                skin="",
                weapon_mods=0,
            )
        )
        self.add(WheelsOnEvent(tick=tick(t), object_id=ObjectId(aircraft), pos=pos))

    def takeoff(self, t: float, aircraft: int, pos: Pos = GROUND) -> None:
        self.add(WheelsOffEvent(tick=tick(t - 4), object_id=ObjectId(aircraft), pos=pos))
        self.add(TakeoffEvent(tick=tick(t), object_id=ObjectId(aircraft), pos=pos))

    def land(self, t: float, aircraft: int, pos: Pos = GROUND) -> None:
        self.add(WheelsOnEvent(tick=tick(t - 1), object_id=ObjectId(aircraft), pos=pos))
        self.add(LandingEvent(tick=tick(t), object_id=ObjectId(aircraft), pos=pos))

    def end(self, t: float, aircraft: int, bot: int, pos: Pos = GROUND) -> None:
        """AType 4 (`aircraft=0` is the PLID:0 shape, with a zero position) and the pilot bot removal."""
        self.add(
            SortieEndEvent(
                tick=tick(t),
                aircraft_id=ObjectId(aircraft),
                bot_id=ObjectId(bot),
                bullets=40,
                shells=20,
                bombs=0,
                rockets=0,
                pos=Pos(0.0, 0.0, 0.0) if aircraft == 0 else pos,
            )
        )

    def remove_bot(self, t: float, bot: int, pos: Pos) -> None:
        self.add(BotRemovedEvent(tick=tick(t), bot_id=ObjectId(bot), pos=pos))

    def disconnect(self, t: float, acct: int) -> None:
        self.add(
            PlayerDisconnectEvent(
                tick=tick(t), account_uuid=AccountUuid(f"account-{acct}"), profile_uuid=ProfileUuid(f"profile-{acct}")
            )
        )

    def area(self, area_id: int, country: int, polygon: tuple[tuple[float, float], ...]) -> None:
        """An enabled influence area (AType 13) with its 2D boundary (AType 14)."""
        self.add(InfluenceAreaEvent(tick=0, area_id=ObjectId(area_id), country=country, enabled=True, bc=(0, 0, 0)))
        self.add(AreaBoundaryEvent(tick=0, area_id=ObjectId(area_id), points=polygon))

    def airfield(self, airfield_id: int, country: int, pos: Pos) -> None:
        self.add(AirfieldEvent(tick=0, airfield_id=ObjectId(airfield_id), country=country, pos=pos))

    def mission_end(self, t: float) -> None:
        self.add(MissionEndEvent(tick=tick(t)))

    def gunner_bailout(self, t: float, bot: int, parent: int, pos: Pos) -> None:
        self.add(BailoutEvent(tick=tick(t), bot_id=ObjectId(bot), parent_id=ObjectId(parent), pos=pos))

    # --- combat ---

    def damage(self, t: float, attacker: int, target: int, amount: float, pos: Pos = FAR) -> None:
        self.add(
            DamageEvent(
                tick=tick(t), damage=amount, attacker_id=ObjectId(attacker), target_id=ObjectId(target), pos=pos
            )
        )

    def kill(self, t: float, attacker: int, target: int, pos: Pos = FAR) -> None:
        self.add(KillEvent(tick=tick(t), attacker_id=ObjectId(attacker), target_id=ObjectId(target), pos=pos))

    def hit(self, t: float, attacker: int, target: int, ammo: str = "BULLET_12-7_USA_API") -> None:
        self.add(HitEvent(tick=tick(t), ammo=ammo, attacker_id=ObjectId(attacker), target_id=ObjectId(target)))

    # --- shortcuts ---

    def fly(
        self,
        aircraft: int,
        bot: int,
        acct: int,
        *,
        aircraft_type: str = "F-86A-5",
        country: int = 601,
        spawn: float = 0,
        up: float = 5,
    ) -> None:
        """Spawn on parking and take off at `up` seconds."""
        self.player(spawn, aircraft, bot, acct, aircraft_type=aircraft_type, country=country)
        self.takeoff(up, aircraft)

    def fly_a(self) -> None:
        self.fly(100, 101, 1)

    def fly_b(self) -> None:
        self.fly(200, 201, 2, aircraft_type="MiG-15bis", country=501)

    def result(self, rules: ReplayRules | None = None) -> MissionResult:
        return run(self.events, FakeCatalog(), rules)


NO = NO_OBJECT


def by_acct(result: MissionResult, acct: int) -> SortieResult:
    (sortie,) = [s for s in result.sorties if s.account_uuid == AccountUuid(f"account-{acct}")]
    return sortie
