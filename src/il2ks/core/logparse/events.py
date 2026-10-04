"""Typed log events: one frozen dataclass per known AType (TD-07, TD-20).

Every event keeps the tick it happened at and an `extra` mapping with any keys the parser didn't map to a field
(for example `MID:` on AType 12 or `TARGETS()` on AType 8), so new log content is kept instead of rejected.
ATypes we don't use (17, 22, 23, 27, 28, 29, and anything unknown) become a `GenericEvent`.

Field names follow doc 12 (design_doc/12_korea_log_format.md); the raw log key is noted in each comment.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple, NewType

ObjectId = NewType("ObjectId", int)
"""In-mission object ID (aircraft, bot, vehicle, store, ...). Only unique within one mission. -1 means none."""

AccountUuid = NewType("AccountUuid", str)
"""Game account UUID (`LOGIN` in AType 10, `USERID` in AType 20/21). Player identity (doc 06)."""

ProfileUuid = NewType("ProfileUuid", str)
"""Profile/nickname UUID (`IDS` in AType 10, `USERNICKID` in AType 20/21)."""

NO_OBJECT = ObjectId(-1)
TICKS_PER_SECOND = 50


class Pos(NamedTuple):
    """A 3D game-world position in meters. y is altitude."""

    x: float
    y: float
    z: float


def _empty_extra() -> MappingProxyType[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base class. `extra` holds unmapped `KEY:value` tokens, verbatim."""

    tick: int
    extra: MappingProxyType[str, str] = field(default_factory=_empty_extra)


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionStartEvent(Event):
    """AType 0."""

    game_date: str  # GDate, raw "1951.9.15"
    game_time: str  # GTime, raw "13:0:0"
    mission_file: str  # MFile
    mission_id: str  # MID (empty in Korea)
    game_type: int  # GType
    countries: MappingProxyType[int, int]  # CNTRS "country:coalition,..." -> {country: coalition}
    settings: str  # SETTS, raw digit string
    mods: int  # MODS
    preset: int  # PRESET
    aqm_id: int  # AQMID


@dataclass(frozen=True, slots=True, kw_only=True)
class HitEvent(Event):
    """AType 1. `ammo` is e.g. `BULLET_12-7_USA_API` or `explosion` (97% of hits; never stored, TD-08)."""

    ammo: str  # AMMO
    attacker_id: ObjectId  # AID
    target_id: ObjectId  # TID


@dataclass(frozen=True, slots=True, kw_only=True)
class ExplosionBurstEvent(Event):
    """Consecutive AType 1 `AMMO:explosion` lines with the same tick and attacker, in log order (speed: they are 72% of
    all lines). Only `parse_lines` yields it, for lines of exactly the shape `T:<tick> AType:1 AMMO:explosion AID:<n>
    TID:<n>`; any other hit line is a `HitEvent`. Consumers treat it as one `HitEvent(ammo="explosion")` per target."""

    attacker_id: ObjectId  # AID
    target_ids: tuple[ObjectId, ...]  # TID of each line (at least one)


@dataclass(frozen=True, slots=True, kw_only=True)
class DamageEvent(Event):
    """AType 2. `damage` is the fraction of the target destroyed by this hit (0..1)."""

    damage: float  # DMG
    attacker_id: ObjectId  # AID (-1 = environment/self)
    target_id: ObjectId  # TID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class KillEvent(Event):
    """AType 3."""

    attacker_id: ObjectId  # AID (-1 = environment/self)
    target_id: ObjectId  # TID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class SortieEndEvent(Event):
    """AType 4. `aircraft_id == 0` (PLID:0) means the pilot wasn't in an aircraft at sortie end (doc 12)."""

    aircraft_id: ObjectId  # PLID
    bot_id: ObjectId  # PID (the pilot bot)
    bullets: int  # BUL (left)
    shells: int  # SH
    bombs: int  # BOMB
    rockets: int  # RCT
    pos: Pos  # unlabelled "(x,y,z)"


@dataclass(frozen=True, slots=True, kw_only=True)
class TakeoffEvent(Event):
    """AType 5."""

    object_id: ObjectId  # PID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class LandingEvent(Event):
    """AType 6."""

    object_id: ObjectId  # PID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionEndEvent(Event):
    """AType 7. Can appear twice, or not at all (doc 12, Timing)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionObjectiveEvent(Event):
    """AType 8. Korea adds trailing `TARGETS() OBJECTS() ...` fields; they go to `extra`."""

    object_id: ObjectId  # OBJID
    pos: Pos  # POS
    coalition: int  # COAL
    objective_type: int  # TYPE
    result: int  # RES
    icon_type: int  # ICTYPE


@dataclass(frozen=True, slots=True, kw_only=True)
class AirfieldEvent(Event):
    """AType 9."""

    airfield_id: ObjectId  # AID
    country: int  # COUNTRY
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerSpawnEvent(Event):
    """AType 10. `in_air`: 0 = air start, 1 = runway, 2 = parking (doc 12)."""

    aircraft_id: ObjectId  # PLID
    bot_id: ObjectId  # PID
    bullets: int  # BUL
    shells: int  # SH
    bombs: int  # BOMB
    rockets: int  # RCT
    pos: Pos  # unlabelled "(x,y,z)"
    profile_uuid: ProfileUuid  # IDS
    account_uuid: AccountUuid  # LOGIN
    name: str  # NAME (may contain spaces)
    aircraft_type: str  # TYPE (log name, e.g. "F-51D", "Turret_IL10")
    country: int  # COUNTRY
    form: int  # FORM
    airfield_id: ObjectId  # FIELD
    in_air: int  # INAIR
    parent_id: ObjectId  # PARENT (-1, or the aircraft for a gunner)
    is_player: bool  # ISPL
    is_tstart: bool  # ISTSTART
    payload_id: int  # PAYLOAD
    fuel: float  # FUEL
    skin: str  # SKIN (may be empty)
    weapon_mods: int  # WM (bitmask)


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupEvent(Event):
    """AType 11."""

    group_id: ObjectId  # GID
    member_ids: tuple[ObjectId, ...]  # IDS
    leader_id: ObjectId  # LID


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectSpawnEvent(Event):
    """AType 12. A known `object_id` re-declared is an update, not a new object (TD-20, doc 12)."""

    object_id: ObjectId  # ID
    object_type: str  # TYPE (may contain spaces and commas)
    country: int  # COUNTRY
    name: str  # NAME
    parent_id: ObjectId  # PID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class InfluenceAreaEvent(Event):
    """AType 13. Korea's `BC` has 3 values."""

    area_id: ObjectId  # AID
    country: int  # COUNTRY
    enabled: bool  # ENABLED
    bc: tuple[int, ...]  # BC(a,b,c)


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaBoundaryEvent(Event):
    """AType 14. Korea's boundary points are 2D `(x, z)`."""

    area_id: ObjectId  # AID
    points: tuple[tuple[float, float], ...]  # BP((x,z),...)


@dataclass(frozen=True, slots=True, kw_only=True)
class LogVersionEvent(Event):
    """AType 15. Header line of every log part."""

    version: int  # VER


@dataclass(frozen=True, slots=True, kw_only=True)
class BotRemovedEvent(Event):
    """AType 16. Bot (crew) deinitialization. For a pilot, `pos` is their final position (bailout rule v2)."""

    bot_id: ObjectId  # BOTID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class BailoutEvent(Event):
    """AType 18. Only for gunners and AI in Korea, never for player pilots (doc 12)."""

    bot_id: ObjectId  # BOTID
    parent_id: ObjectId  # PARENTID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class RoundEndEvent(Event):
    """AType 19."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerConnectEvent(Event):
    """AType 20."""

    account_uuid: AccountUuid  # USERID
    profile_uuid: ProfileUuid  # USERNICKID


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerDisconnectEvent(Event):
    """AType 21."""

    account_uuid: AccountUuid  # USERID
    profile_uuid: ProfileUuid  # USERNICKID


@dataclass(frozen=True, slots=True, kw_only=True)
class GunBurstEvent(Event):
    """AType 24. New in Korea."""

    object_id: ObjectId  # OBJID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class StoreReleaseEvent(Event):
    """AType 25. External store released (bomb, napalm, drop tank). `store_id` is the newly spawned store object."""

    object_id: ObjectId  # OBJID
    pos: Pos  # POS
    store_id: ObjectId  # TID


@dataclass(frozen=True, slots=True, kw_only=True)
class RocketFiredEvent(Event):
    """AType 26. `rocket_id` is the rocket object."""

    object_id: ObjectId  # OBJID
    pos: Pos  # POS
    rocket_id: ObjectId  # TID


@dataclass(frozen=True, slots=True, kw_only=True)
class WheelsOffEvent(Event):
    """AType 30. Fires about 4 s before AType 5."""

    object_id: ObjectId  # ID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class WheelsOnEvent(Event):
    """AType 31. Fires at spawn and before AType 6."""

    object_id: ObjectId  # ID
    pos: Pos  # POS


@dataclass(frozen=True, slots=True, kw_only=True)
class GenericEvent(Event):
    """Any AType without a typed event (17, 22, 23, 27, 28, 29, future ones). All tokens are in `fields` (TD-20)."""

    atype: int
    fields: MappingProxyType[str, str]


type LogEvent = (
    MissionStartEvent
    | HitEvent
    | ExplosionBurstEvent
    | DamageEvent
    | KillEvent
    | SortieEndEvent
    | TakeoffEvent
    | LandingEvent
    | MissionEndEvent
    | MissionObjectiveEvent
    | AirfieldEvent
    | PlayerSpawnEvent
    | GroupEvent
    | ObjectSpawnEvent
    | InfluenceAreaEvent
    | AreaBoundaryEvent
    | LogVersionEvent
    | BotRemovedEvent
    | BailoutEvent
    | RoundEndEvent
    | PlayerConnectEvent
    | PlayerDisconnectEvent
    | GunBurstEvent
    | StoreReleaseEvent
    | RocketFiredEvent
    | WheelsOffEvent
    | WheelsOnEvent
    | GenericEvent
)
"""Everything `logparse` can yield."""
