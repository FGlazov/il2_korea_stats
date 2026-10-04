"""The immutable output of replaying one mission (TD-07).
This is the contract between `core.replay` and `ingest.persist`.

Times are in **ticks** from mission start (50 ticks = 1 s). `ingest` turns them into UTC datetimes using the mission's
`started_at` (TD-15). Field names follow doc 06 (design_doc/06_data_model.md).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from il2ks.core.catalog.loader import GroundCategory, ObjectClass
from il2ks.core.logparse.events import AccountUuid, ObjectId, Pos, ProfileUuid

type Role = Literal["pilot", "gunner"]
type SpawnType = Literal["air", "runway", "parking"]
type Outcome = Literal["landed", "crashed", "shot_down", "ditched", "in_flight", "airborne", "not_taken_off", "unknown"]
type PilotFate = Literal["in_aircraft", "bailed_out", "exited_on_ground", "disconnected", "unknown"]
type PilotFateSource = Literal["event", "inferred", "unknown"]
type PilotStatus = Literal["healthy", "wounded", "dead", "captured"]
type AircraftStatus = Literal["unharmed", "damaged", "destroyed"]
type LossCause = Literal["attacker", "self", "none"]
type LossClass = Literal["player", "ai_aircraft", "ai_gunner", "aaa", "ground", "friendly", "environment", "unknown"]
LOSS_CLASSES: tuple[LossClass, ...] = (
    "player",
    "ai_aircraft",
    "ai_gunner",
    "aaa",
    "ground",
    "friendly",
    "environment",
    "unknown",
)
"""Display order. Same values as `il2ks.db.models.LossClass`."""
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
    """Hits per ammo type (never `explosion`, TD-08), plus the damage attributed to the ammo (FR-WEB-18).

    Named ordnance hit lines (`BOMB_*`, `RKT_*`, `NapalmBullet`) are listed here as hit counts like any other ammo, but
    the damage they and the explosions around them did is reported in `OrdnanceUse`, not here."""

    ammo: str  # e.g. "BULLET_12-7_USA_API"
    hits_given: int = 0
    hits_received: int = 0
    damage_dealt: float = (
        0.0  # damage lines attributed to this ammo by the closest-hit rule (`ReplayRules.ammo_window_s`)
    )
    damage_taken: float = 0.0


@dataclass(frozen=True, slots=True)
class UnattributedDamage:
    """Damage with no hit line of the same attacker and target within the window (fire, secondary damage, ...)."""

    dealt: float = 0.0
    taken: float = 0.0


@dataclass(frozen=True, slots=True)
class OrdnanceUse:
    """One ordnance type of a pilot sortie (FR-WEB-18, doc 02 labelling rule, doc 13).

    `ordnance` is an `ordnance.csv` key (`M65`, `HVAR`, `NAPALM`), a generic key (`bombs_mixed`, `rockets_mixed`: the
    loadout holds several types and the release doesn't name one) or `UNATTRIBUTED_ORDNANCE` (explosions no rule could
    label). Explosion hits are never counted as hits: a detonation is all the explosion lines of one aircraft on one
    tick, and a target counts once per detonation and only if it took damage."""

    ordnance: str
    released: int = 0  # AType 25 (stores) or AType 26 (rocket salvos, not single rockets), drop tanks excluded
    detonations: int = 0
    targets_damaged: int = 0  # one detonation (or direct hit) x one target that took damage
    kills: int = 0  # credited kills whose last damage line came from this ordnance
    damage_dealt: float = 0.0
    damage_taken: float = 0.0  # damage this sortie took from a player's ordnance of this type
    direct_hits: int = 0  # named hit lines given (`BOMB_*`, `RKT_*`): a direct impact, never an explosion line


UNATTRIBUTED_ORDNANCE = "unattributed"


@dataclass(frozen=True, slots=True)
class SingleAttackerKill:
    """An aircraft destroyed where all the damage logged on it came from one attacker, with the gun hits that attacker
    landed on it (FR-WEB-18: hits to destroy). Bullet and shell hit lines only, never explosions or ordnance."""

    victim_type: str  # log name, e.g. "MiG-15bis"
    hits: tuple[tuple[str, int], ...]  # (gun ammo, hits), sorted by ammo


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
    # `hit_given` / `hit_taken` rows only (hits.py): the summed DMG fraction of a burst of damage lines, how many lines,
    # and the ammo of the closest hit (`ammo_kind` "gun" / "ordnance" / "other"; both empty when no hit was near).
    damage: float | None = None
    lines: int = 0
    ammo: str = ""
    ammo_kind: str = ""


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
    assists: int  # assist credits on air and ground victims; always assists_air + assists_ground
    assists_air: int
    assists_ground: int
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
    # Release events up to the sortie end (pilot sorties): AType 25 stores (bombs, napalm, drop tanks) and AType 26
    # rocket salvos. An event is a release command, not one bomb or rocket (measured: it equals the number used in 37%
    # of sorties), so zero events prove "none used" but a positive count gives no number (doc 13, Ammo and resupply).
    store_releases: int = 0
    rocket_salvos: int = 0
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
    # PvE breakdown (FR-WEB-21, doc 13): who is behind the loss, one class per lost sortie (None when nothing was
    # lost); and the air kills split by victim: `kills_air_pvp + kills_air_ai == kills_air`.
    loss_class: LossClass | None = None
    kills_air_pvp: int = 0  # air kills of a player's aircraft
    kills_air_ai: int = 0  # air kills of an AI aircraft
    # Interception (doc 13): air kills of bombers and attackers (`attack.is_interception_victim`), a part of kills_air
    kills_air_intercept: int = 0
    # The server force-ended the sortie at mission end (doc 12, 13): `outcome` then says what state the aircraft was in
    # when the mission ended (`airborne`, `landed`, `ditched`, `not_taken_off`) and the pilot fate is `in_aircraft`.
    ended_by_mission_end: bool = False
    # Ordnance breakdown and unattributed damage (FR-WEB-18): pilot sorties; empty / zero otherwise.
    ordnance: tuple[OrdnanceUse, ...] = ()
    ammo_unattributed: UnattributedDamage = UnattributedDamage()


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
    victim_class: ObjectClass | None = None  # air victims: the catalog class (bomber, attacker, fighter, ...)


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
    single_attacker_kills: tuple[SingleAttackerKill, ...] = ()  # FR-WEB-18: aircraft kills, any victim and attacker
