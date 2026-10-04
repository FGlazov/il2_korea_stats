"""MissionResult -> level-1 rows, then level 2 recomputed for the affected players (FR-ING-5, 6, 9, TD-08).

Order inside `save_mission` (the caller holds the transaction):
1. upsert `Mission` by `(server_uid, mission_uid)` into the tour containing its start (`ingest.tours`, TD-26); remember
   the players and the tour it had before
2. register game objects and countries, upsert players
3. upsert sorties by `(mission, account_uuid, spawn_tick)` (PKs kept), delete sorties that no longer exist
4. upsert PvP `Kill` rows by `(victim_sortie, killer_sortie)`, `PlayerMission` rows (pilots only), mission counters
5. rewrite the mission's `MissionAircraftAmmo` rows (gun hits per destroyed aircraft type, FR-WEB-18)
6. recompute level 2 from level 1 for the mission's old and new players (`aggregates.recompute_players`), per-tour
   rows only for the mission's old and new tour, for the aircraft types whose ammo rows changed
   (`aggregates.recompute_aircraft_ammo`), plus the server-activity day(s) (`ingest.activity`)
7. replay all air-to-air kills for the Elo ratings (`ratings.recompute_ratings`, order-dependent: not per player)
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count

from il2ks.core.catalog.loader import GROUND_CATEGORIES, Catalog, side_of_country
from il2ks.core.logparse.events import TICKS_PER_SECOND, Pos
from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules
from il2ks.core.replay.result import (
    AmmoCounts,
    Counterpart,
    DamageExchange,
    KillResult,
    MissionResult,
    OrdnanceUse,
    SingleAttackerKill,
    SortieResult,
    TimelineEntry,
)
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.core.tours import DEFAULT_TOUR_RULES, TourRules
from il2ks.db.models import (
    TOTAL_AMMO,
    Country,
    GameObject,
    Kill,
    Mission,
    MissionAircraftAmmo,
    Player,
    PlayerMission,
    PlayerSortie,
)
from il2ks.db.site import bump_data_version
from il2ks.ingest.activity import day_of, recompute_days
from il2ks.ingest.aggregates import recompute_aircraft_ammo, recompute_players
from il2ks.ingest.aircraft_stats import mission_aircraft, mission_pairs, recompute_aircraft_stats, recompute_matchups
from il2ks.ingest.counters import COUNTED_ROLES, SORTIE_COUNTERS, clean_counters, counted_sorties
from il2ks.ingest.ratings import recompute_ratings
from il2ks.ingest.scoring import apply_score
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.tours import ensure_tour

log = logging.getLogger(__name__)

PAYLOAD_NAME_MAX = 128  # PlayerSortie.payload_name max_length
DAMAGE_DIGITS = 4  # damage in the ammo JSON is a fraction of an object: 4 digits keep the row small


@dataclass(frozen=True, slots=True)
class MissionMeta:
    """What `ingest` knows about a mission besides the replay result."""

    server_uid: uuid.UUID
    mission_uid: str  # file name timestamp
    started_at: datetime  # UTC, resolved from the local-time file name (TD-15)
    archive_path: str


def save_mission(
    result: MissionResult,
    meta: MissionMeta,
    catalog: Catalog,
    ratings: RatingRules | None = DEFAULT_RULES,
    tours: TourRules = DEFAULT_TOUR_RULES,
    marks: MarkRules | None = DEFAULT_MARK_RULES,
    score: ScoreRules = DEFAULT_SCORE_RULES,
) -> Mission:
    """Upsert one mission's level-1 rows by natural key, then recompute level 2 for the players involved.

    Must run inside the caller's `transaction.atomic()`. Safe to call again for the same mission (re-ingest,
    reprocess): rows that no longer exist are deleted, PKs of rows that still exist are kept (FR-ING-9, FR-WEB-13), and
    level 2 is recomputed for the mission's old players as well as the new ones, so players that dropped out are
    corrected too. The Elo ratings are replayed from all kills afterwards (they depend on the order of games);
    `ratings=None` skips that, and `marks=None` skips the stat thresholds (FR-WEB-22), for a caller that recomputes them
    once after many missions (`reprocess`).

    The mission goes into the tour containing `meta.started_at` under the `[tours]` rules (TD-26); the per-tour level-2
    rows are recomputed for that tour and, when a re-ingest moved the mission, the old one. Elo is all-time.
    Each pilot sortie gets its air and ground score under the `[score]` rules (`score`, FR-WEB-7).
    """
    clock = _Clock(meta.started_at)
    tour = ensure_tour(tours, meta.started_at)
    previous = (
        Mission.objects.filter(server_uid=meta.server_uid, mission_uid=meta.mission_uid)
        .values_list("tour_id", "started_at")
        .first()
    )
    old_tour_id, old_started_at = previous if previous else (None, None)
    mission, created = Mission.objects.update_or_create(
        server_uid=meta.server_uid,
        mission_uid=meta.mission_uid,
        defaults={**_mission_fields(result, meta, clock), "tour": tour},
    )
    old_player_ids: set[int] = set()
    old_aircraft_ids: set[int] = set()
    old_pairs: set[tuple[int, int]] = set()
    if not created:
        old_player_ids = set(PlayerSortie.objects.filter(mission=mission).values_list("player_id", flat=True))
        old_aircraft_ids = mission_aircraft(mission.pk)
        old_pairs = mission_pairs(mission.pk)

    objects = register_game_objects(_object_types(result), catalog)
    register_countries(result.mission.countries, catalog)
    players = _upsert_players(result.sorties, clock)
    sorties = _upsert_sorties(mission, result, clock, catalog, objects, players, score)
    _replace_kills(mission, result.kills, clock, sorties)
    _upsert_player_missions(mission, result.sorties, players)
    _update_mission_counters(mission)
    ammo_aircraft_ids = _replace_aircraft_ammo(mission, result.single_attacker_kills, objects)

    touched_tours = {tour.pk} | _ids(old_tour_id)
    recompute_players(old_player_ids | {p.pk for p in players.values()}, touched_tours)
    recompute_aircraft_ammo(ammo_aircraft_ids)
    recompute_aircraft_stats(old_aircraft_ids | mission_aircraft(mission.pk))  # after the players' PlayerAircraft rows
    recompute_matchups(old_pairs | mission_pairs(mission.pk))
    recompute_days({day_of(meta.started_at)} | ({day_of(old_started_at)} if old_started_at else set()))
    if marks is not None:
        recompute_thresholds(marks, touched_tours)  # FR-WEB-22: after the player rows, once per mission
    if ratings is not None:
        recompute_ratings(ratings)
    bump_data_version()  # TD-28: same transaction as the save
    return mission


def _ids(tour_id: int | None) -> set[int]:
    return set() if tour_id is None else {tour_id}


# --- reference data ---


def register_game_objects(log_names: Iterable[str], catalog: Catalog) -> dict[str, GameObject]:
    """Get or create a `GameObject` per log name (FR-ING-7). Unknown types are stored with `is_known=False`.

    Existing rows: class, playable and known flags follow the catalog (so a catalog update fixes old unknowns), and so
    does the display name, unless an admin edited it (`name_overridden`, TD-24): upgrades refresh the shipped names
    without wiping admin edits.
    """
    wanted = sorted(set(log_names))
    existing = {o.log_name: o for o in GameObject.objects.filter(log_name__in=wanted)}
    for log_name in wanted:
        info = catalog.lookup(log_name)
        obj = existing.get(log_name)
        if obj is None:
            existing[log_name] = GameObject.objects.create(
                log_name=log_name,
                display_name=info.display_name or log_name,
                cls=info.cls,
                propulsion=info.propulsion or "",
                ground_category=info.ground_category or "",
                is_playable=info.is_playable,
                is_known=info.is_known,
            )
            continue
        display_name = obj.display_name if obj.name_overridden else info.display_name or obj.display_name
        category = info.ground_category or ""
        new = (display_name, info.cls, info.propulsion or "", category, info.is_playable, info.is_known)
        if new != (obj.display_name, obj.cls, obj.propulsion, obj.ground_category, obj.is_playable, obj.is_known):
            obj.display_name, obj.cls, obj.propulsion, obj.ground_category, obj.is_playable, obj.is_known = new
            obj.save(update_fields=["display_name", "cls", "propulsion", "ground_category", "is_playable", "is_known"])
    return existing


def register_countries(countries: dict[int, int], catalog: Catalog) -> None:
    """Create missing `Country` rows from CNTRS with the plain side name, REDFOR for 5xx and BLUFOR for 6xx, else the
    coalition name (doc 06). Existing rows are left alone (admin-editable, FR-ADM-5)."""
    for code, coalition in sorted(countries.items()):
        Country.objects.get_or_create(
            code=code, defaults={"coalition": coalition, "display_name": catalog.country_name(code, coalition)}
        )


def _object_types(result: MissionResult) -> set[str]:
    types = set(result.object_types_seen) | set(result.unknown_object_types)
    types.update(s.aircraft_type for s in result.sorties)
    types.update(k.victim_type for k in result.single_attacker_kills)
    for kill in result.kills:
        types.add(kill.victim_type)
        if kill.killer_type is not None:
            types.add(kill.killer_type)
    types.discard("")
    return types


# --- mission ---


class _Clock:
    """Ticks from mission start -> UTC datetimes (50 ticks = 1 s, TD-15)."""

    def __init__(self, started_at: datetime) -> None:
        self.started_at = started_at

    def at(self, tick: int) -> datetime:
        return self.started_at + timedelta(seconds=tick / TICKS_PER_SECOND)

    def at_opt(self, tick: int | None) -> datetime | None:
        return None if tick is None else self.at(tick)


def _mission_fields(result: MissionResult, meta: MissionMeta, clock: _Clock) -> dict[str, object]:
    info = result.mission
    return {
        "mission_file": info.mission_file,
        "file_path": meta.archive_path,
        "started_at": meta.started_at,
        "ended_at": clock.at(info.end_tick),
        "duration_s": info.end_tick / TICKS_PER_SECOND,
        "game_date": info.game_date,
        "game_time": info.game_time,
        "game_type": info.game_type,
        "settings": {"raw": info.settings},
        "countries": {str(code): coalition for code, coalition in sorted(info.countries.items())},
        "log_version": info.log_version,
        "completed_cleanly": info.completed_cleanly,
        "winning_coalition": info.winning_coalition,
    }


def _update_mission_counters(mission: Mission) -> None:
    """Pre-aggregated list/detail counters. Sortie and kill counts use counted (pilot) sorties like the player totals;
    `players_total` counts every player with a sortie of any role. REDFOR/BLUFOR sorties are split by the sortie's
    country code (`side_of_country`), not by coalition number."""
    counted = counted_sorties().filter(mission=mission)
    names = ("sorties", "kills_air", "kills_ground", "friendly_kills")
    totals = clean_counters(counted.aggregate(**{k: SORTIE_COUNTERS[k] for k in names}))
    by_side: dict[str, int] = {}
    for row in counted.values("country").annotate(n=Count("pk")):
        side = side_of_country(row["country"])
        if side is not None:
            by_side[side] = by_side.get(side, 0) + row["n"]
    mission.players_total = PlayerSortie.objects.filter(mission=mission).values("player_id").distinct().count()
    mission.sorties_total = int(totals["sorties"])
    mission.redfor_sorties = by_side.get("redfor", 0)
    mission.blufor_sorties = by_side.get("blufor", 0)
    mission.kills_air = int(totals["kills_air"])
    mission.kills_ground = int(totals["kills_ground"])
    mission.friendly_kills = int(totals["friendly_kills"])
    mission.save(
        update_fields=[
            "players_total",
            "sorties_total",
            "redfor_sorties",
            "blufor_sorties",
            "kills_air",
            "kills_ground",
            "friendly_kills",
        ]
    )


# --- players ---


def _upsert_players(sorties: Iterable[SortieResult], clock: _Clock) -> dict[str, Player]:
    """Get or create a `Player` per account UUID (any role). `recompute_players` refreshes identity fields later."""
    first: dict[str, SortieResult] = {}
    for s in sorted(sorties, key=lambda s: s.spawn_tick):
        first.setdefault(s.account_uuid, s)
    players = {p.account_uuid: p for p in Player.objects.filter(account_uuid__in=list(first))}
    for account, s in first.items():
        if account not in players:
            seen = clock.at(s.spawn_tick)
            players[account] = Player.objects.create(
                account_uuid=account, current_name=s.name, name_lower=s.name.lower(), first_seen=seen, last_seen=seen
            )
    return players


def _upsert_player_missions(mission: Mission, sorties: Iterable[SortieResult], players: dict[str, Player]) -> None:
    """One row per player with at least one pilot sortie (gunner-only players get none until gunner stats exist,
    FR-WEB-14); counters come from counted sorties. Coalition = first pilot sortie's."""
    coalition: dict[int, int] = {}
    for s in sorted(sorties, key=lambda s: (s.spawn_tick, s.index)):
        if s.role in COUNTED_ROLES:
            coalition.setdefault(players[s.account_uuid].pk, s.coalition)
    counters = {
        row["player_id"]: clean_counters(row)
        for row in counted_sorties().filter(mission=mission).values("player_id").annotate(**SORTIE_COUNTERS)
    }
    for player_id, side in coalition.items():
        PlayerMission.objects.update_or_create(
            player_id=player_id, mission=mission, defaults={"coalition": side, **counters[player_id]}
        )
    PlayerMission.objects.filter(mission=mission).exclude(player_id__in=list(coalition)).delete()


# --- sorties ---

_SORTIE_FIELDS = [
    "player",
    "name_at_time",
    "profile_uuid",
    "aircraft",
    "coalition",
    "country",
    "role",
    "spawned_at",
    "took_off_at",
    "landed_at",
    "ended_at",
    "flight_time_s",
    "air_start",
    "spawn_type",
    "payload_id",
    "payload_name",
    "weapon_mods",
    "outcome",
    "pilot_fate",
    "pilot_fate_source",
    "pilot_status",
    "suspected_early_bailout",
    "aircraft_status",
    "damage_taken",
    "disconnected",
    "is_death",
    "is_plane_lost",
    "is_captured",
    "loss_cause",
    "suspected_structural_failure",
    "kills_air",
    "kills_ground",
    "assists",
    "takeoffs",
    "landings",
    "friendly_kills",
    "friendly_hits",
    "friendly_damage",
    "resupplied",
    "taxi_accident",
    "strafed_on_ground",
    "ended_by_mission_end",
    "combat_role",
    "time_on_target_s",
    "kills_ground_tank",
    "kills_ground_vehicle",
    "kills_ground_artillery",
    "kills_ground_aaa",
    "kills_ground_ship",
    "kills_ground_train",
    "kills_ground_building",
    "kills_ground_parked_aircraft",
    "kills_ground_other",
    "kills_ground_static",
    "loss_class",
    "kills_air_pvp",
    "kills_air_ai",
    "air_points",
    "ground_points",
    "ammo",
    "damage_breakdown",
    "timeline",
    "pos_spawn_x",
    "pos_spawn_y",
    "pos_spawn_z",
]


def _upsert_sorties(
    mission: Mission,
    result: MissionResult,
    clock: _Clock,
    catalog: Catalog,
    objects: dict[str, GameObject],
    players: dict[str, Player],
    score: ScoreRules,
) -> dict[int, PlayerSortie]:
    """Upsert by natural key, keeping PKs; delete rows that no longer exist (FR-ING-9). Returns sortie index -> row."""
    existing = {(s.account_uuid, s.spawn_tick): s for s in PlayerSortie.objects.filter(mission=mission)}
    rows: dict[int, PlayerSortie] = {}
    new: list[PlayerSortie] = []
    for s in result.sorties:
        row = existing.pop((s.account_uuid, s.spawn_tick), None)
        if row is None:
            row = PlayerSortie(mission=mission, account_uuid=s.account_uuid, spawn_tick=s.spawn_tick)
            new.append(row)
        _fill_sortie(row, s, clock, catalog, objects[s.aircraft_type], players[s.account_uuid], score)
        rows[s.index] = row
    if existing:
        PlayerSortie.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    PlayerSortie.objects.bulk_create(new)

    # JSON fields link counterpart sorties by PK, known only now that every row has one.
    pks = {index: row.pk for index, row in rows.items()}
    for s in result.sorties:
        row = rows[s.index]
        row.damage_breakdown = [_damage_json(d, pks) for d in s.damage]
        row.timeline = [_timeline_json(t, clock, pks) for t in s.timeline]
    PlayerSortie.objects.bulk_update(list(rows.values()), _SORTIE_FIELDS)
    return rows


def _fill_sortie(
    row: PlayerSortie,
    s: SortieResult,
    clock: _Clock,
    catalog: Catalog,
    aircraft: GameObject,
    player: Player,
    score: ScoreRules,
) -> None:
    payload = catalog.payload(s.aircraft_type, s.payload_id)
    payload_name = payload.readable_name if payload is not None else ""
    row.player = player
    row.name_at_time = s.name
    row.profile_uuid = s.profile_uuid
    row.aircraft = aircraft
    row.coalition = s.coalition
    row.country = s.country
    row.role = s.role
    row.spawned_at = clock.at(s.spawn_tick)
    row.took_off_at = clock.at_opt(s.takeoff_tick)
    row.landed_at = clock.at_opt(s.landing_tick)
    row.ended_at = clock.at(s.end_tick)
    row.flight_time_s = s.flight_time_s
    row.air_start = s.spawn_type == "air"
    row.spawn_type = s.spawn_type
    row.payload_id = s.payload_id
    row.payload_name = payload_name[:PAYLOAD_NAME_MAX]
    row.weapon_mods = s.weapon_mods
    row.outcome = s.outcome
    row.pilot_fate = s.pilot_fate
    row.pilot_fate_source = s.pilot_fate_source
    row.pilot_status = s.pilot_status
    row.suspected_early_bailout = s.suspected_early_bailout
    row.aircraft_status = s.aircraft_status
    row.damage_taken = s.damage_taken
    row.disconnected = s.disconnected
    row.is_death = s.is_death
    row.is_plane_lost = s.is_plane_lost
    row.is_captured = s.is_captured
    row.loss_cause = s.loss_cause
    row.suspected_structural_failure = s.suspected_structural_failure
    row.kills_air = s.kills_air
    row.kills_ground = s.kills_ground
    row.assists = s.assists
    row.takeoffs = s.takeoffs
    row.landings = s.landings
    row.friendly_kills = s.friendly_kills
    row.friendly_hits = s.friendly_hits
    row.friendly_damage = s.friendly_damage
    row.resupplied = s.resupplied
    row.taxi_accident = s.taxi_accident
    row.strafed_on_ground = s.strafed_on_ground
    row.ended_by_mission_end = s.ended_by_mission_end
    row.combat_role = s.combat_role
    row.time_on_target_s = s.time_on_target_s
    for category in GROUND_CATEGORIES:
        setattr(row, f"kills_ground_{category}", s.kills_ground_by_category.get(category, 0))
    row.kills_ground_static = s.kills_ground_static
    row.loss_class = s.loss_class or ""
    row.kills_air_pvp = s.kills_air_pvp
    row.kills_air_ai = s.kills_air_ai
    apply_score(row, score)
    row.ammo = _ammo_json(s)
    row.pos_spawn_x, row.pos_spawn_y, row.pos_spawn_z = s.spawn_pos


# --- JSON fields (plain dicts and lists, doc 06) ---


def _counts_json(c: AmmoCounts) -> dict[str, int]:
    return {"bullets": c.bullets, "shells": c.shells, "bombs": c.bombs, "rockets": c.rockets}


def _ammo_used(s: SortieResult) -> dict[str, int | None]:
    """Ammunition used per type = loaded - left (FR-ING-24). `None` = unknown: the sortie was resupplied (AType 4 only
    describes the last leg), it has no AType 4, the pilot left an aircraft that had been destroyed (its stores read as
    empty), or (bombs) more is left than loaded, which the game does with some payloads (IL-10 bomblets are loaded as
    stations and left as bomblets)."""
    loaded = _counts_json(s.ammo_loaded)
    if s.resupplied or s.ammo_left is None or s.ammo_left_after_loss:
        return dict.fromkeys(loaded)
    left = _counts_json(s.ammo_left)
    used: dict[str, int | None] = {}
    for kind, count in loaded.items():
        used[kind] = None if kind == "bombs" and left[kind] > count else count - left[kind]
    return used


def _ordnance_json(o: OrdnanceUse) -> dict[str, object]:
    return {
        "ordnance": o.ordnance,
        "released": o.released,
        "detonations": o.detonations,
        "targets_damaged": o.targets_damaged,
        "kills": o.kills,
        "damage_dealt": round(o.damage_dealt, DAMAGE_DIGITS),
        "damage_taken": round(o.damage_taken, DAMAGE_DIGITS),
        "direct_hits": o.direct_hits,
    }


def _ammo_json(s: SortieResult) -> dict[str, object]:
    """`PlayerSortie.ammo`, the ammo breakdown of one sortie (FR-WEB-18, doc 13). Shape (keys are only ever added, so
    rows written by an older version still read, until the next reprocess):

        {"loaded": {"bullets", "shells", "bombs", "rockets"},          # AType 10
         "left": {... same} | null,                                    # AType 4; null = no AType 4
         "used": {... same, each int | null},                          # loaded - left; null = unknown (resupplied...)
         "hits": [{"ammo": "BULLET_12-7_USA_API",                      # every non-explosion hit line, per ammo name
                   "hits_given": int, "hits_received": int,            #   (named bomb/rocket/napalm lines included)
                   "damage_dealt": float, "damage_taken": float}],     # damage attributed to this ammo (guns only)
         "unattributed": {"dealt": float, "taken": float},             # damage with no hit within `ammo_window_s`
         "ordnance": [{"ordnance": "M65",                              # catalog key | "bombs_mixed" | "rockets_mixed"
                       "released": int,                                #   | "unattributed" (unlabelled explosions)
                       "detonations": int,                             # explosion ticks, never counted as hits
                       "targets_damaged": int,                         # detonation x target that took damage
                       "kills": int, "direct_hits": int,               # direct_hits = named BOMB_/RKT_ lines given
                       "damage_dealt": float, "damage_taken": float}]} # attributed to this ordnance

    Damage dealt (or taken) in the sortie = the sum of `hits[].damage_*`, `ordnance[].damage_*` and `unattributed`.
    Only pilot sorties carry `ordnance`. Damage fractions are rounded to 4 digits to keep the row small."""
    return {
        "loaded": _counts_json(s.ammo_loaded),
        "left": None if s.ammo_left is None else _counts_json(s.ammo_left),
        "used": _ammo_used(s),
        "hits": [
            {
                "ammo": h.ammo,
                "hits_given": h.hits_given,
                "hits_received": h.hits_received,
                "damage_dealt": round(h.damage_dealt, DAMAGE_DIGITS),
                "damage_taken": round(h.damage_taken, DAMAGE_DIGITS),
            }
            for h in s.ammo_hits
        ],
        "unattributed": {
            "dealt": round(s.ammo_unattributed.dealt, DAMAGE_DIGITS),
            "taken": round(s.ammo_unattributed.taken, DAMAGE_DIGITS),
        },
        "ordnance": [_ordnance_json(o) for o in s.ordnance],
    }


def _pos_json(pos: Pos | None) -> list[float] | None:
    return None if pos is None else [pos.x, pos.y, pos.z]


def _counterpart_json(c: Counterpart, pks: dict[int, int]) -> dict[str, object]:
    return {
        "object_type": c.object_type,
        "coalition": c.coalition,
        "sortie_id": None if c.sortie_index is None else pks.get(c.sortie_index),
    }


def _damage_json(d: DamageExchange, pks: dict[int, int]) -> dict[str, object]:
    return {
        "counterpart": _counterpart_json(d.counterpart, pks),
        "damage_dealt": d.damage_dealt,
        "damage_taken": d.damage_taken,
        "hits_dealt": d.hits_dealt,
        "hits_taken": d.hits_taken,
    }


def _timeline_json(t: TimelineEntry, clock: _Clock, pks: dict[int, int]) -> dict[str, object]:
    return {
        "tick": t.tick,
        "at": clock.at(t.tick).isoformat(),
        "kind": t.kind,
        "detail": t.detail,
        "pos": _pos_json(t.pos),
        "counterpart": None if t.counterpart is None else _counterpart_json(t.counterpart, pks),
    }


# --- kills ---


def _dedupe_kills(kills: Iterable[KillResult]) -> dict[tuple[int, int], KillResult]:
    """PvP kill results by `(victim sortie index, killer sortie index)`. If the replay ever emits two for one pair, keep
    the `kill` over an `assist` (else the first) and log it: the natural key allows one row per pair (doc 06)."""
    best: dict[tuple[int, int], KillResult] = {}
    for k in kills:
        if k.killer_sortie_index is None or k.victim_sortie_index is None:
            continue
        key = (k.victim_sortie_index, k.killer_sortie_index)
        have = best.get(key)
        if have is None:
            best[key] = k
            continue
        log.warning(
            "two kill results for victim sortie %d and killer sortie %d (%s at tick %d, %s at tick %d): keeping one",
            *key,
            have.credit,
            have.tick,
            k.credit,
            k.tick,
        )
        if have.credit != "kill" and k.credit == "kill":
            best[key] = k
    return best


def _replace_kills(
    mission: Mission, kills: Iterable[KillResult], clock: _Clock, sorties: dict[int, PlayerSortie]
) -> None:
    """`Kill` is PvP only (doc 06): rows where both killer and victim are player sorties.

    Upserted by `(victim_sortie, killer_sortie)` so re-ingesting keeps PKs; `credit` and `tick` are plain attributes
    updated in place. Rows not in the result are deleted."""
    existing = {(k.victim_sortie_id, k.killer_sortie_id): k for k in Kill.objects.filter(mission=mission)}
    changed: list[Kill] = []
    new: list[Kill] = []
    for (victim_index, killer_index), k in _dedupe_kills(kills).items():
        killer, victim = sorties[killer_index], sorties[victim_index]
        row = existing.pop((victim.pk, killer.pk), None)
        if row is None:
            row = Kill(mission=mission, killer_sortie=killer, victim_sortie=victim)
            new.append(row)
        else:
            changed.append(row)
        pos = _pos_json(k.pos)
        row.tick = k.tick
        row.time = clock.at(k.tick)
        row.credit = k.credit
        row.is_friendly = k.is_friendly
        row.via = k.via
        row.pos_x, row.pos_y, row.pos_z = (None, None, None) if pos is None else pos
    Kill.objects.filter(pk__in=[k.pk for k in existing.values()]).delete()
    Kill.objects.bulk_update(changed, ["tick", "time", "credit", "is_friendly", "via", "pos_x", "pos_y", "pos_z"])
    Kill.objects.bulk_create(new)


# --- hits to destroy (FR-WEB-18) ---


def aircraft_ammo_totals(kills: Iterable[SingleAttackerKill]) -> dict[tuple[str, str], tuple[int, int]]:
    """`(victim type, ammo) -> (kills, hits)` for one mission. A kill counts for an ammo if that ammo hit at least once;
    `TOTAL_AMMO` sums all gun ammo and counts every kill."""
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for kill in kills:
        rows = [(TOTAL_AMMO, sum(n for _, n in kill.hits)), *kill.hits]
        for ammo, hits in rows:
            done, total = totals.get((kill.victim_type, ammo), (0, 0))
            totals[(kill.victim_type, ammo)] = (done + 1, total + hits)
    return totals


def _replace_aircraft_ammo(
    mission: Mission, kills: Iterable[SingleAttackerKill], objects: dict[str, GameObject]
) -> set[int]:
    """Rewrite the mission's `MissionAircraftAmmo` rows. Returns the aircraft ids whose level-2 rows may change: the
    types the mission had before and the ones it has now."""
    totals = aircraft_ammo_totals(kills)
    old_ids = set(MissionAircraftAmmo.objects.filter(mission=mission).values_list("aircraft_id", flat=True))
    MissionAircraftAmmo.objects.filter(mission=mission).delete()
    MissionAircraftAmmo.objects.bulk_create(
        MissionAircraftAmmo(mission=mission, aircraft=objects[victim], ammo=ammo, kills=n, hits=hits)
        for (victim, ammo), (n, hits) in sorted(totals.items())
    )
    return old_ids | {objects[victim].pk for victim, _ in totals}
