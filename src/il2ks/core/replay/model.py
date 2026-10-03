"""Mutable replay state: tracked objects and in-progress sorties (TD-07).

Everything here is internal to `core.replay`. Rules read these records at resolve time (`finish()` / `snapshot()`),
so lookahead rules (bailout, structural failure, abandoned-aircraft credit) see the whole history of an object.
"""

import math
import re
from dataclasses import dataclass, field

from il2ks.core.catalog.loader import ObjectInfo
from il2ks.core.logparse.events import AccountUuid, ObjectId, Pos, ProfileUuid
from il2ks.core.replay.result import AmmoCounts, Role, SpawnType

_BLOCK_SUFFIX = re.compile(r"\[[^\]]*\]$")


def normalize_type(object_type: str) -> str:
    """Strip the static block group suffix: `Factory block E[36731,0]` -> `Factory block E` (doc 12, AType 12 `MID`).

    The suffix changes between re-declarations of the same object, so it isn't part of the object type."""
    return _BLOCK_SUFFIX.sub("", object_type).strip()


def is_bot_type(object_type: str) -> bool:
    """Crew bots (`BotPlanePilot_*`, `BotGunner_*`, ...). They're people, never kills or aircraft."""
    return object_type.lower().startswith("bot")


def distance(a: Pos, b: Pos) -> float:
    return math.dist(a, b)


def is_zero_pos(pos: Pos) -> bool:
    """AType 4 `PLID:0` carries `(0,0,0)`, and some spawns are logged at the origin: not a real position."""
    return pos.x == 0.0 and pos.y == 0.0 and pos.z == 0.0


@dataclass(slots=True)
class DamageRecord:
    """One AType 2 line. `attacker` is resolved when the line is read (IDs can be reused later in a mission)."""

    tick: int
    attacker: "TrackedObject | None"  # None = environment / self (AID:-1)
    amount: float


@dataclass(slots=True)
class HitRecord:
    """One AType 1 line that isn't `explosion` (TD-08)."""

    tick: int
    attacker: "TrackedObject | None"
    ammo: str


@dataclass(slots=True, eq=False)
class TrackedObject:
    """One in-mission object. Identity is the Python object, not the `ObjectId`: IDs get reused (doc 12)."""

    object_id: ObjectId
    object_type: str  # normalized log name ("" for a placeholder of an undeclared ID)
    country: int
    coalition: int | None
    info: ObjectInfo
    is_bot: bool
    parent: "TrackedObject | None" = None
    children: "list[TrackedObject]" = field(default_factory=list["TrackedObject"])  # bots, turrets
    sortie: "SortieState | None" = None
    pos: Pos | None = None  # last known position
    airborne: bool = False
    damage_log: list[DamageRecord] = field(default_factory=list[DamageRecord])
    hit_log: list[HitRecord] = field(default_factory=list[HitRecord])
    destroyed_tick: int | None = None
    destroyed_by: "TrackedObject | None" = None
    destroyed_pos: Pos | None = None
    destroyed_airborne: bool = False
    ground_contact_after_destroyed_tick: int | None = None  # first AType 31/6 after AType 3 (FR-ING-17)
    removed_tick: int | None = None  # AType 16 (bots)
    removed_pos: Pos | None = None
    bailout_tick: int | None = None  # AType 18 (gunners and AI only in Korea)
    bailout_pos: Pos | None = None

    @property
    def root(self) -> "TrackedObject":
        obj = self
        seen = 0
        while obj.parent is not None and seen < 16:  # guard against parent cycles in broken logs
            obj = obj.parent
            seen += 1
        return obj

    def set_parent(self, parent: "TrackedObject | None") -> None:
        if parent is None or parent is self or parent is self.parent:
            return
        if self.parent is not None and self in self.parent.children:
            self.parent.children.remove(self)
        self.parent = parent
        parent.children.append(self)

    def update_pos(self, pos: Pos) -> None:
        if not is_zero_pos(pos):
            self.pos = pos


@dataclass(slots=True, eq=False)
class SortieState:
    """One player sortie while it's being replayed (AType 10 to its end)."""

    index: int
    account_uuid: AccountUuid
    profile_uuid: ProfileUuid
    name: str
    aircraft_type: str
    aircraft_id: ObjectId
    bot_id: ObjectId
    country: int
    coalition: int
    role: Role
    parent_sortie: "SortieState | None"
    spawn_tick: int
    spawn_type: SpawnType
    spawn_pos: Pos
    payload_id: int
    weapon_mods: int
    fuel: float
    skin: str
    ammo_loaded: AmmoCounts
    vehicle: TrackedObject  # PLID: the aircraft for a pilot, the turret for a gunner
    bot: TrackedObject  # PID: the pilot or gunner bot
    takeoff_tick: int | None = None
    landing_tick: int | None = None
    takeoffs: int = 0
    landings: int = 0
    flight_changes: list[tuple[int, bool]] = field(default_factory=list[tuple[int, bool]])  # AType 5/6 (tick, airborne)
    end_tick: int | None = None
    end_aircraft_id: ObjectId | None = None  # AType 4 PLID; None = no AType 4 (yet)
    end_pos: Pos | None = None
    ended_by_removal: bool = False  # AType 16 without AType 4 (doc 12: disconnect shape)
    airborne_at_end: bool = False
    ammo_left: AmmoCounts | None = None
    disconnect_ticks: list[int] = field(default_factory=list[int])  # AType 21 for this account while it was current
    captured_pos: Pos | None = None  # last landing position, checked against enemy areas (TD-21)

    @property
    def airframe(self) -> TrackedObject:
        """The aircraft this sortie flies in: the vehicle for a pilot, the parent aircraft for a gunner."""
        if self.role == "gunner" and self.vehicle.parent is not None:
            return self.vehicle.parent
        return self.vehicle

    @property
    def is_open(self) -> bool:
        return self.end_tick is None


def owner_sortie(obj: TrackedObject | None) -> SortieState | None:
    """The player sortie an object acts for: its own, or the nearest ancestor's (an AI turret on a player's aircraft)."""
    seen = 0
    while obj is not None and seen < 16:
        if obj.sortie is not None:
            return obj.sortie
        obj = obj.parent
        seen += 1
    return None
