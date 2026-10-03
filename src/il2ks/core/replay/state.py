"""The streaming replay state machine (TD-07).

`feed()` only records facts (objects, sorties, damage, kills, positions). All game rules run at resolve time in
`resolve.py`, for both `snapshot()` (provisional) and `finish()` (final), so streaming and batch give the same result.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from il2ks.core.catalog.loader import Catalog, ObjectInfo
from il2ks.core.logparse.events import (
    NO_OBJECT,
    TICKS_PER_SECOND,
    AccountUuid,
    AirfieldEvent,
    AreaBoundaryEvent,
    BailoutEvent,
    BotRemovedEvent,
    DamageEvent,
    GunBurstEvent,
    HitEvent,
    InfluenceAreaEvent,
    KillEvent,
    LandingEvent,
    LogEvent,
    LogVersionEvent,
    MissionEndEvent,
    MissionObjectiveEvent,
    MissionStartEvent,
    ObjectId,
    ObjectSpawnEvent,
    PlayerDisconnectEvent,
    PlayerSpawnEvent,
    Pos,
    RocketFiredEvent,
    SortieEndEvent,
    StoreReleaseEvent,
    TakeoffEvent,
    WheelsOffEvent,
    WheelsOnEvent,
)
from il2ks.core.replay.areas import Airfield, Area
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.model import (
    GROUND_CLASSES,
    DamageRecord,
    HitRecord,
    MissionFacts,
    SortieState,
    TrackedObject,
    is_bot_type,
    is_plausible_pos,
    is_zero_pos,
    normalize_type,
)
from il2ks.core.replay.resolve import resolve_mission
from il2ks.core.replay.result import AmmoCounts, MissionInfo, MissionResult, SortieResult, SpawnType

logger = logging.getLogger(__name__)

_SPAWN_TYPES: dict[int, SpawnType] = {0: "air", 1: "runway", 2: "parking"}


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A provisional view of an in-progress mission (it2 "online now"). Unresolved fields may change."""

    tick: int
    sorties: tuple[SortieResult, ...]
    mission: MissionInfo | None = None  # None only when resolving failed
    open_sorties: frozenset[int] = frozenset()  # `SortieResult.index` of sorties with no end (AType 4) logged yet


class Replay:
    """Feed events one at a time, then `finish()` (TD-07). Deterministic: same events in, same result out."""

    def __init__(self, catalog: Catalog, rules: ReplayRules | None = None) -> None:
        self._catalog = catalog
        self._rules = rules or ReplayRules()
        self._info_cache: dict[str, ObjectInfo] = {}
        self._objects: dict[ObjectId, TrackedObject] = {}
        self._facts = MissionFacts()
        self._sortie_by_bot: dict[ObjectId, SortieState] = {}
        self._sortie_by_vehicle: dict[ObjectId, SortieState] = {}
        self._sortie_by_account: dict[AccountUuid, SortieState] = {}

    # --- public API -------------------------------------------------------------------------------------------------

    def feed(self, event: LogEvent) -> None:
        facts = self._facts
        facts.last_tick = max(facts.last_tick, event.tick)
        match event:
            case MissionStartEvent():
                facts.start = event
                facts.countries = dict(event.countries)
            case ObjectSpawnEvent():
                self._on_object_spawn(event)
            case PlayerSpawnEvent():
                self._on_player_spawn(event)
            case HitEvent():
                self._on_hit(event)
            case DamageEvent():
                self._on_damage(event)
            case KillEvent():
                self._on_kill(event)
            case SortieEndEvent():
                self._on_sortie_end(event)
            case TakeoffEvent():
                self._on_takeoff(event.tick, event.object_id, event.pos)
            case LandingEvent():
                self._on_landing(event.tick, event.object_id, event.pos)
            case WheelsOffEvent():
                self._on_wheels(event.tick, event.object_id, event.pos, airborne=True)
            case WheelsOnEvent():
                self._on_wheels(event.tick, event.object_id, event.pos, airborne=False)
            case BotRemovedEvent():
                self._on_bot_removed(event)
            case BailoutEvent():
                bot = self._get(event.bot_id)
                if bot is not None and bot.bailout_tick is None:
                    bot.bailout_tick = event.tick
                    bot.bailout_pos = event.pos
            case PlayerDisconnectEvent():
                sortie = self._sortie_by_account.get(event.account_uuid)
                if sortie is not None:
                    sortie.disconnect_ticks.append(event.tick)
            case MissionEndEvent():
                facts.mission_end_ticks.append(event.tick)
            case MissionObjectiveEvent():
                # Derived from il2_stats (MIT), see NOTICE: MissionReport.event_mission_result
                if event.objective_type == 0 and event.coalition != 0 and event.result and facts.winner is None:
                    facts.winner = event.coalition
            case InfluenceAreaEvent():
                area = facts.areas.get(event.area_id)
                coalition = facts.countries.get(event.country)
                if area is None:
                    facts.areas[event.area_id] = Area(coalition=coalition, enabled=event.enabled)
                else:
                    area.coalition = coalition
                    area.enabled = event.enabled
            case AreaBoundaryEvent():
                area = facts.areas.setdefault(event.area_id, Area(coalition=None, enabled=False))
                area.boundary = event.points
            case AirfieldEvent():
                facts.airfields[event.airfield_id] = Airfield(facts.countries.get(event.country), event.pos)
            case LogVersionEvent():
                if facts.log_version is None:
                    facts.log_version = event.version
            case GunBurstEvent():
                obj = self._objects.get(event.object_id)
                if obj is not None:
                    obj.update_pos(event.pos)
            case StoreReleaseEvent() | RocketFiredEvent():
                obj = self._objects.get(event.object_id)
                if obj is not None:
                    obj.update_pos(event.pos)
                    if not is_zero_pos(event.pos):
                        obj.releases.append((event.tick, event.pos))  # time on target (attack.py)
            case _:
                pass

    def snapshot(self) -> Snapshot:
        """Provisional sorties so far. Never raises: a live view must not stop the watcher."""
        try:
            result = resolve_mission(self._facts, self._rules, final=False)
        except Exception:  # pragma: no cover - defensive, a bug here must not break live views
            logger.exception("replay snapshot failed at tick %d", self._facts.last_tick)
            return Snapshot(tick=self._facts.last_tick, sorties=())
        open_sorties = frozenset(s.index for s in self._facts.sorties if s.is_open)
        return Snapshot(
            tick=self._facts.last_tick, sorties=result.sorties, mission=result.mission, open_sorties=open_sorties
        )

    def finish(self) -> MissionResult:
        return resolve_mission(self._facts, self._rules, final=True)

    # --- objects ----------------------------------------------------------------------------------------------------

    def _info(self, object_type: str) -> ObjectInfo:
        info = self._info_cache.get(object_type)
        if info is None:
            info = self._catalog.lookup(object_type)
            self._info_cache[object_type] = info
            if object_type:
                self._facts.types_seen[object_type] = info.is_known
        return info

    def _new_object(
        self, object_id: ObjectId, object_type: str, country: int, parent: TrackedObject | None, *, bot: bool
    ) -> TrackedObject:
        obj = TrackedObject(
            object_id=object_id,
            object_type=object_type,
            country=country,
            coalition=self._facts.countries.get(country),
            info=self._info(object_type),
            is_bot=bot or is_bot_type(object_type),
        )
        obj.set_parent(parent)
        self._objects[object_id] = obj
        self._facts.objects.append(obj)
        return obj

    def _get(self, object_id: ObjectId) -> TrackedObject | None:
        """Known object, or a placeholder for an ID that was never declared (logs do that; il2_stats did the same)."""
        if object_id == NO_OBJECT or object_id == 0:
            return None
        obj = self._objects.get(object_id)
        if obj is None:
            obj = self._new_object(object_id, "", 0, None, bot=False)
        return obj

    def _on_object_spawn(self, event: ObjectSpawnEvent) -> None:
        """AType 12. A known ID with the same type is an update/re-link, never a replacement (TD-20, doc 12).

        A different type means the game reused the ID for a new object (seen in the samples), so that one is new."""
        object_type = normalize_type(event.object_type)
        parent = self._objects.get(event.parent_id) if event.parent_id not in (NO_OBJECT, event.object_id) else None
        existing = self._objects.get(event.object_id)
        reused = existing is not None and existing.sortie is None and existing.destroyed_tick is not None
        if existing is not None and existing.sortie is not None and existing.sortie.end_tick is not None:
            # An ended sortie's aircraft or bot ID declared again later is a new object, or a later AType 3 on it would
            # count as the old sortie's loss or death (the shot-down shape re-declares within a second of AType 4)
            late = existing.sortie.end_tick + round(self._rules.post_end_destroy_window_ground_s * TICKS_PER_SECOND)
            reused = event.tick > late
        if (
            existing is not None
            and not reused  # a destroyed AI object's ID comes back as a new object (15k such re-uses in the samples)
            and (existing.object_type in ("", object_type) or (existing.sortie is not None and existing.sortie.is_open))
        ):
            if existing.object_type == "":
                existing.object_type = object_type
                existing.info = self._info(object_type)
                existing.is_bot = existing.is_bot or is_bot_type(object_type)
            existing.country = event.country
            existing.coalition = self._facts.countries.get(event.country)
            if parent is not None:
                existing.set_parent(parent)  # PID:-1 on a pilot re-declaration doesn't break the link (doc 12)
            existing.update_pos(event.pos)
            self._note_declared_pos(existing, event)
            self._track_ground(existing, event.tick, event.pos)
            return
        obj = self._new_object(event.object_id, object_type, event.country, parent, bot=False)
        obj.update_pos(event.pos)
        self._note_declared_pos(obj, event)
        self._track_ground(obj, event.tick, event.pos)

    @staticmethod
    def _note_declared_pos(obj: TrackedObject, event: ObjectSpawnEvent) -> None:
        """A bot's latest AType 12 position, the fallback for a pilot's final position (`fate.pilot_final_pos`)."""
        if obj.is_bot and not is_zero_pos(event.pos) and is_plausible_pos(event.pos):
            obj.declared_tick = event.tick
            obj.declared_pos = event.pos

    @staticmethod
    def _track_ground(obj: TrackedObject, tick: int, pos: Pos) -> None:
        """Remember where a ground object (a possible bombing target) was and when. AType 2/3 `POS` is the target's
        position, so these lines follow vehicles that move. A repeat of the last position isn't stored."""
        if obj.info.cls not in GROUND_CLASSES or is_zero_pos(pos):
            return
        if not obj.track or obj.track[-1][1] != pos:
            obj.track.append((tick, pos))

    # --- sorties ----------------------------------------------------------------------------------------------------

    def _on_player_spawn(self, event: PlayerSpawnEvent) -> None:
        """AType 10: a new sortie. `ISPL:0` still is a player (player gunners log it, doc 12 samples)."""
        facts = self._facts
        aircraft_type = normalize_type(event.aircraft_type)
        parent_aircraft = self._get(event.parent_id)
        role = "gunner" if event.parent_id != NO_OBJECT or aircraft_type.lower().startswith("turret_") else "pilot"
        vehicle = self._fresh_for_sortie(event.aircraft_id, aircraft_type, event.country, parent_aircraft, bot=False)
        bot = self._fresh_for_sortie(event.bot_id, "", event.country, vehicle, bot=True)
        if bot.parent is None:
            bot.set_parent(vehicle)
        if role == "gunner" and vehicle.parent is None:
            vehicle.set_parent(parent_aircraft)
        vehicle.update_pos(event.pos)
        spawn_type = _SPAWN_TYPES.get(event.in_air, "parking")
        if role == "pilot":
            vehicle.airborne = spawn_type == "air"
        sortie = SortieState(
            index=len(facts.sorties),
            account_uuid=event.account_uuid,
            profile_uuid=event.profile_uuid,
            name=event.name,
            aircraft_type=aircraft_type,
            aircraft_id=event.aircraft_id,
            bot_id=event.bot_id,
            country=event.country,
            coalition=facts.countries.get(event.country, 0),
            role=role,
            parent_sortie=self._sortie_by_vehicle.get(event.parent_id) if role == "gunner" else None,
            spawn_tick=event.tick,
            spawn_type=spawn_type,
            spawn_pos=event.pos,
            payload_id=event.payload_id,
            weapon_mods=event.weapon_mods,
            fuel=event.fuel,
            skin=event.skin,
            ammo_loaded=AmmoCounts(event.bullets, event.shells, event.bombs, event.rockets),
            vehicle=vehicle,
            bot=bot,
        )
        if spawn_type == "air" and role == "pilot":
            vehicle.flight_changes.append((event.tick, True))
        vehicle.sortie = sortie
        bot.sortie = sortie
        facts.sorties.append(sortie)
        self._sortie_by_bot[event.bot_id] = sortie
        self._sortie_by_vehicle[event.aircraft_id] = sortie
        self._sortie_by_account[event.account_uuid] = sortie

    def _fresh_for_sortie(
        self, object_id: ObjectId, object_type: str, country: int, parent: TrackedObject | None, *, bot: bool
    ) -> TrackedObject:
        """The AType 12 object a new sortie uses, or a new one if that ID still belongs to an older sortie."""
        obj = self._objects.get(object_id)
        type_matches = obj is not None and (
            obj.object_type in ("", object_type) or (bot and not object_type and obj.is_bot)
        )
        if obj is not None and obj.sortie is None and obj.destroyed_tick is None and type_matches:
            if obj.object_type == "" and object_type:
                obj.object_type = object_type
                obj.info = self._info(object_type)
            obj.is_bot = obj.is_bot or bot
            return obj
        declared_type = obj.object_type if obj is not None and not object_type else object_type
        return self._new_object(object_id, declared_type, country, parent, bot=bot)

    def _on_sortie_end(self, event: SortieEndEvent) -> None:
        """AType 4. `PLID:0` duplicates are ignored (only the first AType 4 of a sortie counts, doc 12)."""
        sortie = self._sortie_by_bot.get(event.bot_id)
        if sortie is None or sortie.end_aircraft_id is not None:
            return
        if sortie.end_tick is None:
            sortie.end_tick = event.tick
            sortie.airborne_at_end = sortie.airframe.airborne
        sortie.end_aircraft_id = event.aircraft_id
        sortie.end_pos = event.pos
        sortie.ended_by_removal = False
        sortie.ammo_left = AmmoCounts(event.bullets, event.shells, event.bombs, event.rockets)

    def _on_bot_removed(self, event: BotRemovedEvent) -> None:
        """AType 16. For a sortie without AType 4 this ends it (doc 12: the disconnect shape)."""
        bot = self._objects.get(event.bot_id)
        if bot is None:
            return
        if bot.removed_tick is None:
            bot.removed_tick = event.tick
            bot.removed_pos = event.pos if is_plausible_pos(event.pos) else None
        sortie = bot.sortie
        if sortie is not None and sortie.bot is bot and sortie.end_tick is None:
            sortie.end_tick = event.tick
            sortie.ended_by_removal = True
            sortie.airborne_at_end = sortie.airframe.airborne

    # --- flight state -----------------------------------------------------------------------------------------------

    def _on_wheels(self, tick: int, object_id: ObjectId, pos: Pos, *, airborne: bool) -> None:
        obj = self._objects.get(object_id)
        if obj is None:
            return
        obj.update_pos(pos)
        if obj.destroyed_tick is not None:
            if not airborne and obj.ground_contact_after_destroyed_tick is None:
                obj.ground_contact_after_destroyed_tick = tick
            return
        obj.airborne = airborne

    def _on_takeoff(self, tick: int, object_id: ObjectId, pos: Pos) -> None:
        obj = self._objects.get(object_id)
        if obj is None:
            return
        sortie = obj.sortie
        if obj.destroyed_tick is not None and sortie is not None and sortie.vehicle is obj and sortie.is_open:
            self._undo_destruction(obj)  # a destroyed aircraft can't take off: that AType 3 was a reset
        self._on_wheels(tick, object_id, pos, airborne=True)
        obj.takeoffs.append((tick, pos))
        obj.flight_changes.append((tick, True))

    def _undo_destruction(self, obj: TrackedObject) -> None:
        """The game logs an AType 3 (`AID:-1`, preceded by 1.0 damage and an AType 12 re-declaration with a non-`-1`
        `MID`) for some player aircraft on the parking spot, then lets them take off minutes later (27 aircraft in the
        210 sample missions). A takeoff after the destruction proves it wasn't one: forget the destruction and the
        environment damage logged on the same tick, so the sortie flies on as if nothing happened."""
        tick = obj.destroyed_tick
        if obj in self._facts.destroyed:
            self._facts.destroyed.remove(obj)
        obj.damage_log = [r for r in obj.damage_log if not (r.attacker is None and r.tick == tick)]
        obj.destroyed_tick = None
        obj.destroyed_by = None
        obj.destroyed_pos = None
        obj.destroyed_airborne = False
        obj.ground_contact_after_destroyed_tick = None

    def _on_landing(self, tick: int, object_id: ObjectId, pos: Pos) -> None:
        obj = self._objects.get(object_id)
        if obj is None:
            return
        self._on_wheels(tick, object_id, pos, airborne=False)
        obj.landings.append((tick, pos))
        obj.flight_changes.append((tick, False))

    # --- combat -----------------------------------------------------------------------------------------------------

    def _on_hit(self, event: HitEvent) -> None:
        if event.ammo.lower() == "explosion":  # never counted as a hit (TD-08, FR-WEB-18)
            return
        target = self._get(event.target_id)
        if target is None:
            return
        target.hit_log.append(HitRecord(event.tick, self._get(event.attacker_id), event.ammo))

    def _on_damage(self, event: DamageEvent) -> None:
        if event.damage <= 0:  # il2_stats ignored zero damage too (log bug)
            return
        target = self._get(event.target_id)
        if target is None or target.destroyed_tick is not None:
            return  # Derived from il2_stats (MIT), see NOTICE: no damage after destruction
        target.update_pos(event.pos)
        self._track_ground(target, event.tick, event.pos)
        attacker = self._get(event.attacker_id)
        target.damage_log.append(DamageRecord(event.tick, attacker, event.damage))

    def _on_kill(self, event: KillEvent) -> None:
        target = self._get(event.target_id)
        if target is None or target.destroyed_tick is not None:
            return
        target.update_pos(event.pos)
        self._track_ground(target, event.tick, event.pos)
        target.destroyed_tick = event.tick
        target.destroyed_by = self._get(event.attacker_id)
        target.destroyed_pos = event.pos
        target.destroyed_airborne = target.airborne
        self._facts.destroyed.append(target)


def run(events: Iterable[LogEvent], catalog: Catalog, rules: ReplayRules | None = None) -> MissionResult:
    """Batch mode: feed every event, then finish."""
    replay = Replay(catalog, rules)
    for event in events:
        replay.feed(event)
    return replay.finish()
