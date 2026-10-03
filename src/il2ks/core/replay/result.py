"""The immutable output of replaying one mission (TD-07).
This is the contract between `core.replay` and `ingest.persist`.

Times are in **ticks** from mission start (50 ticks = 1 s). `ingest` turns them into UTC datetimes using the mission's
`started_at` (TD-15). Field names follow doc 06 (design_doc/06_data_model.md).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from il2ks.core.catalog.loader import GroundCategory
from il2ks.core.logparse.events import AccountUuid, ObjectId, Pos, ProfileUuid

type Role = Literal["pilot", "gunner"]
type SpawnType = Literal["air", "runway", "parking"]
type Outcome = Literal[
    "landed", "crashed", "shot_down", "ditched", "in_flight", "not_taken_off", "mission_ended", "unknown"
]
type PilotFate = Literal["in_aircraft", "bailed_out", "exited_on_ground", "mission_ended", "disconnected", "unknown"]
type PilotFateSource = Literal["event", "inferred", "unknown"]
type PilotStatus = Literal["healthy", "wounded", "dead", "captured"]
type AircraftStatus = Literal["unharmed", "damaged", "destroyed"]
type LossCause = Literal["attacker", "self", "none"]
type KillCredit = Literal["kill", "assist", "shared"]
type KillVia = Literal["direct", "abandoned_aircraft", "disconnect"]
type TargetKind = Literal["air", "ground"]
type CombatRole = Literal["air_superiority", "attack"]


@dataclass(frozen=True, slots=True)
class AmmoCounts:
    """Ammunition counts from AType 10 (loaded) and AType 4 (left)."""

    bullets: int = 0
    shells: int = 0
    bombs: int = 0
    rockets: int = 0


@dataclass(frozen=True, slots=True)
class AmmoHits:
    """Hits per ammo type (never `explosion`, TD-08). Detailed attribution (FR-WEB-18) is iteration 1.x."""

    ammo: str  # e.g. "BULLET_12-7_USA_API"
    hits_given: int = 0
    hits_received: int = 0


@dataclass(frozen=True, slots=True)
class Counterpart:
    """Who a sortie dealt damage to or took damage from. `sortie_index` is set for a player sortie."""

    object_type: str  # log name, e.g. "MiG-15bis", "M46 Patton"
    sortie_index: int | None = None
    coalition: int | None = None


@dataclass(frozen=True, slots=True)
class DamageExchange:
    """Damage and hits between this sortie and one counterpart (doc 06 `damage_breakdown`)."""

    counterpart: Counterpart
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    hits_dealt: int = 0
    hits_taken: int = 0


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One key event on the sortie timeline (FR-WEB-6). Positions only on key events, no track (TD-08)."""

    tick: int
    # e.g. "spawn", "takeoff", "landing", "kill", "assist", "damaged", "shot_down", "killed" (crew died, aircraft not
    # lost), "died", "bailout", "sortie_end"
    kind: str
    detail: str = ""
    pos: Pos | None = None
    counterpart: Counterpart | None = None


@dataclass(frozen=True, slots=True)
class SortieResult:
    """One player sortie: from AType 10 (spawn) to its end (doc 06 `PlayerSortie`)."""

    index: int  # position in MissionResult.sorties; stable for the same input
    account_uuid: AccountUuid
    profile_uuid: ProfileUuid
    name: str
    aircraft_type: str  # log name (TYPE in AType 10)
    aircraft_id: ObjectId
    bot_id: ObjectId
    country: int
    coalition: int
    role: Role
    parent_sortie_index: int | None  # gunner -> the pilot's sortie, if known
    spawn_tick: int
    spawn_type: SpawnType
    spawn_pos: Pos
    payload_id: int
    weapon_mods: int
    fuel: float
    skin: str
    takeoff_tick: int | None
    landing_tick: int | None
    end_tick: int
    flight_time_s: float
    takeoffs: int
    landings: int
    outcome: Outcome
    pilot_fate: PilotFate
    pilot_fate_source: PilotFateSource
    pilot_status: PilotStatus
    aircraft_status: AircraftStatus
    damage_taken: float  # 0..1, aircraft damage from all sources (clamped)
    disconnected: bool
    is_death: bool  # counts as a death in totals (FR-ING-21 for disconnects)
    is_plane_lost: bool  # counts as an aircraft lost in totals
    is_captured: bool
    suspected_early_bailout: bool  # FR-ING-14 rule v2
    loss_cause: LossCause  # FR-ING-17
    suspected_structural_failure: bool  # FR-ING-17 definition v2
    kills_air: int
    kills_ground: int
    assists: int
    ammo_loaded: AmmoCounts
    ammo_left: AmmoCounts | None  # None when the sortie had no AType 4
    ammo_hits: tuple[AmmoHits, ...] = ()
    damage: tuple[DamageExchange, ...] = ()
    timeline: tuple[TimelineEntry, ...] = ()
    # Friendly fire, tracked apart from kills_*/assists (same non-zero coalition, never the sortie's own objects)
    friendly_kills: int = 0  # `kill` credits (not assists) on friendly objects, from KillResult.is_friendly
    friendly_hits: int = 0  # non-explosion hit lines this sortie put on friendly objects
    friendly_damage: float = 0.0  # sum of the damage this sortie did to friendly objects
    resupplied: bool = False  # FR-ING-24: a landing followed by another takeoff, and `ReplayRules.resupply_allowed`
    # AType 4 came more than `ReplayRules.ammo_left_after_loss_s` after the aircraft was destroyed: `ammo_left` reads
    # as empty stores, so "ammo used" can't be derived from it (doc 13, Ammo and resupply).
    ammo_left_after_loss: bool = False
    # Ground losses (doc 13, OQ-32 answer): the aircraft was lost on the ground, by the sortie itself or by an attacker.
    taxi_accident: bool = False  # lost before its first takeoff, loss_cause "self"
    strafed_on_ground: bool = False  # lost on the ground (before takeoff, or parked after landing) to an attacker
    # Sortie role from the loadout (FR-WEB-19/20, doc 13): None for gunners.
    combat_role: CombatRole | None = None
    # Time on target (FR-WEB-20, doc 13): attack sorties only (None otherwise); 0.0 when no release was near a target.
    time_on_target_s: float | None = None
    # Ground kills by what they were (OQ-33, doc 13): the categories sum to `kills_ground`; `kills_ground_static` is
    # how many of those were static objects (a separate axis: a static truck is a "vehicle" and static).
    kills_ground_by_category: Mapping[GroundCategory, int] = field(default_factory=dict[GroundCategory, int])
    kills_ground_static: int = 0


@dataclass(frozen=True, slots=True)
class KillResult:
    """One kill or assist credit where **at least one side is a player sortie**.

    `ingest` stores only PvP rows in `Kill` (both sides set, doc 06); the rest feed sortie counters and timelines."""

    tick: int
    victim_object_id: ObjectId
    victim_type: str  # log name
    victim_kind: TargetKind
    victim_coalition: int | None
    victim_sortie_index: int | None
    killer_sortie_index: int | None
    killer_type: str | None  # log name; None for environment (AID:-1) without credit
    credit: KillCredit
    via: KillVia
    is_friendly: bool
    pos: Pos | None
    killer_coalition: int | None = None  # of the credited party; lets a timeline mark a friendly shoot-down
    victim_ground_category: GroundCategory | None = None  # ground victims only ("other" for an uncatalogued type)
    victim_is_static: bool = False


@dataclass(frozen=True, slots=True)
class MissionInfo:
    """Mission metadata from AType 0, 7, 8, 15 (doc 06 `Mission`)."""

    mission_file: str  # MFile
    game_date: str  # raw GDate "1951.9.15"
    game_time: str  # raw GTime "13:0:0"
    game_type: int
    settings: str
    countries: dict[int, int]  # country -> coalition, from CNTRS (never hard-coded, doc 12)
    log_version: int | None
    end_tick: int  # first AType 7, else the last tick seen
    last_tick: int
    completed_cleanly: bool  # AType 7 seen
    winning_coalition: int | None


@dataclass(frozen=True, slots=True)
class MissionResult:
    """Everything `ingest` needs to write the level-1 rows of one mission."""

    mission: MissionInfo
    sorties: tuple[SortieResult, ...]
    kills: tuple[KillResult, ...]
    object_types_seen: frozenset[str] = field(
        default_factory=frozenset[str]
    )  # for auto-registering unknowns (FR-ING-7)
    unknown_object_types: frozenset[str] = field(default_factory=frozenset[str])  # not in the catalog
