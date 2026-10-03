"""Mutable replay state: tracked objects, in-progress sorties and mission facts (TD-07).

Everything here is internal to `core.replay`. `Replay.feed()` only records facts into these records. Rules read them at
resolve time (`finish()` / `snapshot()`), so lookahead rules (bailout, structural failure, abandoned-aircraft credit)
see the whole history of an object.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from il2ks.core.catalog.loader import Catalog, LoadoutItem, ObjectClass, ObjectInfo, canonical_type_name
from il2ks.core.logparse.events import AccountUuid, MissionStartEvent, ObjectId, Pos, ProfileUuid
from il2ks.core.replay.areas import Airfield, Area
from il2ks.core.replay.result import AmmoCounts, Role, SpawnType

_BLOCK_SUFFIX = re.compile(r"\[[^\]]*\]$")

GROUND_CLASSES: frozenset[ObjectClass] = frozenset({"tank", "vehicle", "aaa", "ship", "static"})
"""Object classes a bomb, napalm or rocket release can be aimed at (time on target, `attack.py`)."""


def normalize_type(object_type: str) -> str:
    """Strip per-instance parts of a log type: the static block group suffix (`Factory block E[36731,0]` ->
    `Factory block E`, doc 12, AType 12 `MID`) and instance numbers (`CParachute_2361344` -> `CParachute`).

    They change between objects of the same kind, so they aren't part of the object type (otherwise every parachute
    would be registered as its own `GameObject`)."""
    return canonical_type_name(_BLOCK_SUFFIX.sub("", object_type).strip())


def is_bot_type(object_type: str) -> bool:
    """Crew bots (`BotPlanePilot_*`, `BotGunner_*`, ...). They're people, never kills or aircraft."""
    return object_type.lower().startswith("bot")


def distance(a: Pos, b: Pos) -> float:
    return math.dist(a, b)


def is_zero_pos(pos: Pos) -> bool:
    """AType 4 `PLID:0` carries `(0,0,0)`, and some spawns are logged at the origin: not a real position."""
    return pos.x == 0.0 and pos.y == 0.0 and pos.z == 0.0


POS_MAX_ABS_XZ_M = 1_000_000.0
POS_MIN_Y_M = -1_000.0
POS_MAX_Y_M = 20_000.0


def is_plausible_pos(pos: Pos) -> bool:
    """False for a garbage position (3 AType 16 lines in the 210 samples, all of dead pilots): the map is far smaller
    than 1,000 km and nothing flies above 20 km or below -1 km. The one place these bounds live."""
    return abs(pos.x) <= POS_MAX_ABS_XZ_M and abs(pos.z) <= POS_MAX_ABS_XZ_M and POS_MIN_Y_M <= pos.y <= POS_MAX_Y_M


@dataclass(slots=True)
class DamageRecord:
    """One AType 2 line with damage > 0. `attacker` is resolved when the line is read (IDs can be reused later)."""

    tick: int
    attacker: "TrackedObject | None"  # None = environment / self (AID:-1)
    amount: float


@dataclass(slots=True)
class HitRecord:
    """One AType 1 line that isn't `explosion` (TD-08)."""

    tick: int
    attacker: "TrackedObject | None"
    ammo: str
    target: "TrackedObject | None" = None  # set for the hits a player's aircraft gave (`TrackedObject.given_hits`)


@dataclass(slots=True)
class Detonation:
    """The explosion hit lines one player aircraft produced on one tick, one entry per target (FR-WEB-18).

    Kept in memory only (TD-08: explosion lines are never stored or counted as hits). The log writes 1-9 lines per
    detonation and target, so lines are collapsed to the set of targets they touched."""

    tick: int
    targets: "dict[ObjectId, TrackedObject]"


type ReleaseClass = Literal["store", "rocket"]
"""AType 25 (bomb, napalm, drop tank, flare, JATO) or AType 26 (a rocket salvo)."""


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
    flight_changes: list[tuple[int, bool]] = field(default_factory=list[tuple[int, bool]])  # AType 5/6, air spawns
    takeoffs: list[tuple[int, Pos]] = field(default_factory=list[tuple[int, Pos]])  # AType 5
    landings: list[tuple[int, Pos]] = field(default_factory=list[tuple[int, Pos]])  # AType 6
    damage_log: list[DamageRecord] = field(default_factory=list[DamageRecord])
    hit_log: list[HitRecord] = field(default_factory=list[HitRecord])
    destroyed_tick: int | None = None
    destroyed_by: "TrackedObject | None" = None
    destroyed_pos: Pos | None = None
    destroyed_airborne: bool = False
    ground_contact_after_destroyed_tick: int | None = None  # first AType 31/6 after AType 3 (FR-ING-17)
    track: list[tuple[int, Pos]] = field(default_factory=list[tuple[int, Pos]])  # ground objects: (tick, position)
    releases: list[tuple[int, Pos]] = field(default_factory=list[tuple[int, Pos]])  # AType 25/26 by this aircraft
    store_releases: list[tuple[int, ReleaseClass]] = field(
        default_factory=list[tuple[int, ReleaseClass]]
    )  # same, FR-WEB-18
    # Player aircraft only (FR-WEB-18): what it put on others. Explosion hits collapsed per tick, and the non-explosion
    # hit lines it gave (guns and named ordnance), both in log (= tick) order.
    detonations: list[Detonation] = field(default_factory=list[Detonation])
    given_hits: list[HitRecord] = field(default_factory=list[HitRecord])
    removed_tick: int | None = None  # AType 16 (bots)
    removed_pos: Pos | None = None  # None too when the logged position is garbage (`is_plausible_pos`)
    declared_tick: int | None = None  # the latest AType 12 for a bot ...
    declared_pos: Pos | None = None  # ... and where it said the bot was: the fallback when AType 16 has no position
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

    def airborne_at(self, tick: int) -> bool:
        """Airborne state at `tick`, from AType 5/6 and air spawns (wheel events only drive the live flag)."""
        state = False
        for change_tick, airborne in self.flight_changes:
            if change_tick > tick:
                break
            state = airborne
        return state


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
    loadout: tuple[LoadoutItem, ...] | None = None  # from the payload file; None = payload unknown (FR-WEB-18)
    end_tick: int | None = None
    end_aircraft_id: ObjectId | None = None  # AType 4 PLID; None = no AType 4 (yet)
    end_pos: Pos | None = None
    ended_by_removal: bool = False  # AType 16 without AType 4 (doc 12: disconnect shape)
    airborne_at_end: bool = False
    ammo_left: AmmoCounts | None = None
    disconnect_ticks: list[int] = field(default_factory=list[int])  # AType 21 for this account while it was current

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
    """The player sortie an object acts for: its own, or the nearest ancestor's (an AI turret of a player aircraft)."""
    seen = 0
    while obj is not None and seen < 16:
        if obj.sortie is not None:
            return obj.sortie
        obj = obj.parent
        seen += 1
    return None


type Party = SortieState | TrackedObject
"""Who acted: a player sortie, or (for AI and the environment's machines) the root object of the actor."""


def party_of(obj: TrackedObject) -> Party:
    sortie = owner_sortie(obj)
    return sortie if sortie is not None else obj.root


@dataclass(slots=True)
class MissionFacts:
    """Everything `feed()` recorded about the mission so far."""

    start: MissionStartEvent | None = None
    countries: dict[int, int] = field(default_factory=dict[int, int])
    log_version: int | None = None
    last_tick: int = 0
    mission_end_ticks: list[int] = field(default_factory=list[int])
    winner: int | None = None
    areas: dict[ObjectId, Area] = field(default_factory=dict[ObjectId, Area])
    airfields: dict[ObjectId, Airfield] = field(default_factory=dict[ObjectId, Airfield])
    sorties: list[SortieState] = field(default_factory=list[SortieState])
    objects: list[TrackedObject] = field(default_factory=list[TrackedObject])  # every object ever created
    destroyed: list[TrackedObject] = field(default_factory=list[TrackedObject])  # in AType 3 order
    types_seen: dict[str, bool] = field(default_factory=dict[str, bool])  # normalized log name -> in catalog
    catalog: Catalog = field(default_factory=Catalog)  # ordnance names for FR-WEB-18 (empty: nothing is ordnance)

    @property
    def first_mission_end(self) -> int | None:
        return self.mission_end_ticks[0] if self.mission_end_ticks else None
