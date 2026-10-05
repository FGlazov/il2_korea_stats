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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

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
    MIX_SEPARATOR,
    TOTAL_AMMO,
    Country,
    GameObject,
    Kill,
    Mission,
    MissionAircraftAmmo,
    MissionAircraftAmmoMix,
    Player,
    PlayerMission,
    PlayerSortie,
)
from il2ks.db.site import bump_data_version
from il2ks.ingest.achievements import recompute_holders
from il2ks.ingest.activity import day_of, recompute_days
from il2ks.ingest.aggregates import recompute_aircraft_ammo, recompute_players
from il2ks.ingest.aircraft_stats import (
    mission_aircraft,
    mission_pairs,
    recompute_aircraft_stats,
    recompute_matchups,
    recompute_payload_elo,
)
from il2ks.ingest.counters import COUNTED_ROLES, COUNTER_FIELDS, SORTIE_COUNTERS, clean_counters, counted_sorties
from il2ks.ingest.dbutil import update_partial_rows, update_rows
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
    live: bool = False  # a provisional save of the running mission (FR-ING-15); the final save leaves it False


class DuplicateSortieError(ValueError):
    """The replay produced the same sortie (account, spawn tick) twice, which the natural key forbids. In practice the
    log file holds the mission's text twice. The message says what to do; the runner records it without a traceback."""


@dataclass(slots=True)
class Touched:
    """What a level-1 save changed, i.e. what level 2 must recompute (the mission's old and new players, tours, ...).
    Provisional passes collect it until the next level-2 pass (`[live] aggregates_interval_s`, FR-ING-15)."""

    players: set[int] = field(default_factory=set[int])
    tours: set[int] = field(default_factory=set[int])
    aircraft: set[int] = field(default_factory=set[int])  # for the per-aircraft stats
    pairs: set[tuple[int, int]] = field(default_factory=set[tuple[int, int]])
    ammo_aircraft: set[int] = field(default_factory=set[int])
    days: set[date] = field(default_factory=set[date])

    def update(self, other: "Touched") -> None:
        self.players |= other.players
        self.tours |= other.tours
        self.aircraft |= other.aircraft
        self.pairs |= other.pairs
        self.ammo_aircraft |= other.ammo_aircraft
        self.days |= other.days

    def __bool__(self) -> bool:
        return bool(self.players or self.tours or self.aircraft or self.pairs or self.ammo_aircraft or self.days)


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

    `meta.live`: a provisional save of the running mission (`ingest.live`, FR-ING-15). The same rows by the same natural
    keys, so every URL stays the same until the final save. The live tracker uses `save_level1` and `apply_level2`
    separately (own intervals); this function with `meta.live` is the same thing in one go, without ratings (the Elo
    replay ignores the kills of a live mission anyway, `ratings._games`).
    """
    mission, touched = save_level1(result, meta, catalog, tours, score)
    # with ratings, `recompute_ratings` refreshes the loadout Elo, and the holder counts come once after it
    apply_level2(touched, payload_elo=ratings is None, holders=ratings is None)
    if ratings is not None:
        recompute_ratings(ratings)  # may change medals (Elo peaks)
        recompute_holders()  # FR-WEB-26: once, after every step that changes the medal rows
    if marks is not None:  # FR-WEB-22: after the player rows and the Elo replay (the Elo marks read the ratings)
        recompute_thresholds(marks, touched.tours)
    bump_data_version()  # TD-28: same transaction as the save
    return mission


def apply_level2(touched: Touched, *, payload_elo: bool = True, holders: bool = True) -> None:
    """Recompute level 2 from level 1 for what a save touched. Inside the caller's transaction.

    `payload_elo`: refresh the loadouts' and weapon-mod sets' average pilot Elo. `recompute_ratings` does it too, so a
    save that replays the ratings afterwards passes False; a live pass (no ratings, FR-ING-15) needs it, or its new
    loadout and mod rows would have no Elo until the final save. `holders`: the achievement holder counts, likewise
    left to the caller when the ratings run afterwards (Elo peaks change medals; one count after both)."""
    recompute_players(touched.players, touched.tours)
    if holders:
        recompute_holders()  # FR-WEB-26: the overview counts, after the players' medal rows
    recompute_aircraft_ammo(touched.ammo_aircraft)
    # after the players' PlayerAircraft / PlayerTourAircraft rows
    recompute_aircraft_stats(touched.aircraft, touched.tours)
    if payload_elo:
        recompute_payload_elo()
    recompute_matchups(touched.pairs)
    recompute_days(touched.days)


def apply_batch_end(touched: Touched, all_tours: Iterable[int], ratings: RatingRules, marks: MarkRules) -> None:
    """The end of a batched run (`ingest.batch`): level 2 for what is still pending, then, once, the Elo ratings (they
    replay every kill in mission order, so the order the missions were saved in does not matter), the holder counts and
    the thresholds of every tour any save of the batch touched. The same steps, in the same order, as `save_mission`
    does per mission. Inside the caller's transaction."""
    apply_level2(touched, payload_elo=False, holders=False)  # the ratings below refresh the loadout Elo, holders follow
    recompute_ratings(ratings)  # may change medals (Elo peaks)
    recompute_holders()
    recompute_thresholds(marks, all_tours)


def save_level1(
    result: MissionResult,
    meta: MissionMeta,
    catalog: Catalog,
    tours: TourRules = DEFAULT_TOUR_RULES,
    score: ScoreRules = DEFAULT_SCORE_RULES,
) -> tuple[Mission, Touched]:
    """The level-1 half of `save_mission`: mission, players, sorties, kills, PlayerMission rows, mission counters and
    the per-mission ammo rows. Returns what level 2 has to recompute for it."""
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

    touched = Touched(
        players=old_player_ids | {p.pk for p in players.values()},
        tours={tour.pk} | _ids(old_tour_id),
        aircraft=old_aircraft_ids | mission_aircraft(mission.pk),
        pairs=old_pairs | mission_pairs(mission.pk),
        ammo_aircraft=ammo_aircraft_ids,
        days={day_of(meta.started_at)} | ({day_of(old_started_at)} if old_started_at else set()),
    )
    return mission, touched


def discard_provisional_mission(mission: Mission) -> None:
    """Delete a provisional mission that will never be finished (its log files are gone, or the admin switched live
    sorties off) and bring level 2 back to what it was without it (FR-ING-15). Inside the caller's transaction.
    Players stay (never deleted); their counters are recomputed. Ratings need no work: live kills never counted."""
    assert mission.is_live, "only provisional missions are discarded this way"
    player_ids = set(PlayerSortie.objects.filter(mission=mission).values_list("player_id", flat=True))
    aircraft_ids = mission_aircraft(mission.pk)
    pairs = mission_pairs(mission.pk)
    ammo_ids = set(MissionAircraftAmmo.objects.filter(mission=mission).values_list("aircraft_id", flat=True))
    ammo_ids |= set(MissionAircraftAmmoMix.objects.filter(mission=mission).values_list("aircraft_id", flat=True))
    tours = _ids(mission.tour_id)
    started = mission.started_at
    mission.delete()
    recompute_players(player_ids, tours)
    recompute_holders()
    recompute_aircraft_ammo(ammo_ids)
    recompute_aircraft_stats(aircraft_ids, tours)
    recompute_matchups(pairs)
    recompute_days({day_of(started)})
    bump_data_version()


def _ids(tour_id: int | None) -> set[int]:
    return set() if tour_id is None else {tour_id}


# --- reference data ---


def register_game_objects(log_names: Iterable[str], catalog: Catalog) -> dict[str, GameObject]:
    """Get or create a `GameObject` per type (FR-ING-7). Unknown types are stored with `is_known=False`.

    The result is keyed by the log's own spelling. Known types are stored under the catalog's spelling, so `Il-10` and
    `IL-10` (both occur in real logs) share one row and one aircraft page; unknown types keep the log's spelling.

    Existing rows: class, playable and known flags follow the catalog (so a catalog update fixes old unknowns), and so
    does the display name, unless an admin edited it (`name_overridden`, TD-24): upgrades refresh the shipped names
    without wiping admin edits.
    """
    wanted = sorted(set(log_names))
    infos = {name: catalog.lookup(name) for name in wanted}
    stored = {name: (info.log_name if info.is_known else name) for name, info in infos.items()}
    rows = {o.log_name: o for o in GameObject.objects.filter(log_name__in=set(stored.values()))}
    for key in sorted(set(stored.values())):
        info = catalog.lookup(key)
        obj = rows.get(key)
        if obj is None:
            rows[key] = GameObject.objects.create(
                log_name=key,
                display_name=info.display_name or key,
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
    return {name: rows[key] for name, key in stored.items()}


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
        "is_live": meta.live,
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
    existing = {row.player_id: row for row in PlayerMission.objects.filter(mission=mission)}
    changed: list[PlayerMission] = []
    new: list[PlayerMission] = []
    for player_id, side in coalition.items():
        values = {"coalition": side, **counters[player_id]}
        row = existing.pop(player_id, None)
        if row is None:
            new.append(PlayerMission(player_id=player_id, mission=mission, **values))
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    PlayerMission.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()  # no pilot sortie any more
    update_rows(PlayerMission, changed, ["coalition", *COUNTER_FIELDS])
    PlayerMission.objects.bulk_create(new)


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
    "pilot_damage",
    "disconnected",
    "is_death",
    "is_plane_lost",
    "is_captured",
    "loss_cause",
    "suspected_structural_failure",
    "kills_air",
    "kills_ground",
    "assists",
    "assists_air",
    "assists_ground",
    "takeoffs",
    "landings",
    "friendly_kills",
    "friendly_hits",
    "friendly_damage",
    "resupplied",
    "rounds_fired",
    "gun_hits_air",
    "gun_hits_ground",
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
    "kills_air_intercept",
    "rams",
    "first_blood",
    "multi_kill",
    "air_points",
    "ground_points",
    "ammo",
    "damage_breakdown",
    "timeline",
    "pos_spawn_x",
    "pos_spawn_y",
    "pos_spawn_z",
]


_LINK_FIELDS = ["damage_breakdown", "timeline"]  # JSON fields that name other sorties by PK, filled after the insert


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
    seen: set[tuple[str, int]] = set()
    for s in result.sorties:
        if (s.account_uuid, s.spawn_tick) in seen:
            raise DuplicateSortieError(
                f"The log contains the same sortie twice (account {s.account_uuid}, tick {s.spawn_tick}): is the "
                "mission's text duplicated in the file? Nothing was saved for this mission. Remove the duplicated "
                "part of the log file, then run il2ks reprocess --mission for it."
            )
        seen.add((s.account_uuid, s.spawn_tick))
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
    # New rows were just inserted with every other field, so only the links are written; existing rows get everything.
    new_ids = {id(row) for row in new}
    update_rows(PlayerSortie, [r for r in rows.values() if id(r) not in new_ids], _SORTIE_FIELDS)
    # Just the two JSON columns of rows we hold completely: a plain UPDATE, an upsert would send all ~65 columns again.
    update_partial_rows(PlayerSortie, [r for r in new if r.damage_breakdown or r.timeline], _LINK_FIELDS)
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
    row.pilot_damage = s.pilot_damage
    row.disconnected = s.disconnected
    row.is_death = s.is_death
    row.is_plane_lost = s.is_plane_lost
    row.is_captured = s.is_captured
    row.loss_cause = s.loss_cause
    row.suspected_structural_failure = s.suspected_structural_failure
    row.kills_air = s.kills_air
    row.kills_ground = s.kills_ground
    row.assists = s.assists
    row.assists_air = s.assists_air
    row.assists_ground = s.assists_ground
    row.takeoffs = s.takeoffs
    row.landings = s.landings
    row.friendly_kills = s.friendly_kills
    row.friendly_hits = s.friendly_hits
    row.friendly_damage = s.friendly_damage
    row.resupplied = s.resupplied
    row.rounds_fired = s.rounds_fired
    row.gun_hits_air = s.gun_hits_air
    row.gun_hits_ground = s.gun_hits_ground
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
    row.kills_air_intercept = s.kills_air_intercept
    row.rams = s.rams
    row.first_blood = s.first_blood
    row.multi_kill = s.multi_kill
    apply_score(row, score)
    row.ammo = _ammo_json(s)
    row.pos_spawn_x, row.pos_spawn_y, row.pos_spawn_z = s.spawn_pos


# --- JSON fields (plain dicts and lists, doc 06) ---


def _counts_json(c: AmmoCounts) -> dict[str, int]:
    return {"bullets": c.bullets, "shells": c.shells, "bombs": c.bombs, "rockets": c.rockets}


def _ammo_used_estimate(s: SortieResult, used: dict[str, int | None]) -> dict[str, int]:
    """OQ-101: an estimate of the bombs and rockets used, for the sortie whose "left" was written after the aircraft was
    lost (so "used" is unknown) but which released something. A release event is a command that can drop a pair (or a
    salvo), so no count follows from it; "all loaded" matched the trusted record in 92% of bomb and 87% of rocket
    sorties. Kept apart from `used`, which stays exact (or null): nothing sums or ranks the estimate. Guns: none.
    A resupplied sortie gets none (it may have released more than it carried at take-off)."""
    if not s.ammo_left_after_loss or s.resupplied:
        return {}
    loaded = _counts_json(s.ammo_loaded)
    released = {"bombs": s.store_releases, "rockets": s.rocket_salvos}
    return {kind: loaded[kind] for kind, n in released.items() if n > 0 and loaded[kind] > 0 and used.get(kind) is None}


def _ammo_used(s: SortieResult) -> dict[str, int | None]:
    """Ammunition used per type = loaded - left (FR-ING-24). `None` = unknown: the sortie was resupplied (AType 4 only
    describes the last leg), it has no AType 4, the pilot left an aircraft that had been destroyed (its stores read as
    empty), or (bombs) more is left than loaded, which the game does with some payloads (IL-10 bomblets are loaded as
    stations and left as bomblets).

    Where that leaves bombs or rockets unknown, **no release event** in the whole sortie means none was used (doc 13).
    Where "left" is trusted it is kept, also if it disagrees with the releases. Release events are no counts (one event
    is a release command that can drop a pair, a rocket event is a salvo): a positive number gives no "used"."""
    loaded = _counts_json(s.ammo_loaded)
    unreleased = {"bombs": s.store_releases == 0, "rockets": s.rocket_salvos == 0}
    trusted = not (s.resupplied or s.ammo_left is None or s.ammo_left_after_loss)
    left = _counts_json(s.ammo_left) if s.ammo_left is not None else loaded
    used: dict[str, int | None] = {}
    for kind, count in loaded.items():
        if trusted and not (kind == "bombs" and left[kind] > count):
            used[kind] = count - left[kind]
        else:
            used[kind] = 0 if unreleased.get(kind, False) else None
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
         "used": {... same, each int | null},                          # loaded - left; null = unknown (resupplied...);
                                                                       #   bombs, rockets: 0 when none was released
         "used_estimate": {"bombs": int, "rockets": int},              # OQ-101: only the kinds that were released while
                                                                       #   "used" is null after a loss; = all loaded
         "left_after_loss": bool,                                      # AType 4 came long after the loss: unreliable
         "releases": {"stores": int, "rocket_salvos": int},            # AType 25 / 26 events (commands, not counts)
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
    used = _ammo_used(s)
    return {
        "loaded": _counts_json(s.ammo_loaded),
        "left": None if s.ammo_left is None else _counts_json(s.ammo_left),
        "used": used,
        "used_estimate": _ammo_used_estimate(s, used),
        "left_after_loss": s.ammo_left_after_loss,
        "releases": {"stores": s.store_releases, "rocket_salvos": s.rocket_salvos},
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
    """One `PlayerSortie.timeline` entry. The hit rows (`hit_given` / `hit_taken`, doc 14) add `damage` (summed
    DMG fraction, 4 digits), `lines` and, when a hit lay near, `ammo` (log name, or ordnance key with `ammo_kind`
    "ordnance"), and `target_role` "crew" when the damaged object was a pilot / crew bot; other rows and rows from
    before the hit rows have none of these keys."""
    entry: dict[str, object] = {
        "tick": t.tick,
        "at": clock.at(t.tick).isoformat(),
        "kind": t.kind,
        "detail": t.detail,
        "pos": _pos_json(t.pos),
        "counterpart": None if t.counterpart is None else _counterpart_json(t.counterpart, pks),
    }
    if t.damage is not None:
        entry["damage"] = round(t.damage, DAMAGE_DIGITS)
        entry["lines"] = t.lines
        if t.ammo:
            entry["ammo"] = t.ammo
            if t.ammo_kind != "gun":
                entry["ammo_kind"] = t.ammo_kind
        if t.target_role:
            entry["target_role"] = t.target_role
    return entry


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
    update_rows(Kill, changed, ["tick", "time", "credit", "is_friendly", "via", "pos_x", "pos_y", "pos_z"])
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


def aircraft_ammo_mix_totals(kills: Iterable[SingleAttackerKill]) -> dict[tuple[str, str, str], tuple[int, int]]:
    """`(victim type, mix key, ammo) -> (instances, hits)` for one mission: kills grouped by the set of gun ammo that
    hit. Each mix has a row per member ammo (its hits) and a `TOTAL_AMMO` row (all hits)."""
    totals: dict[tuple[str, str, str], tuple[int, int]] = {}
    for kill in kills:
        if not kill.hits:
            continue
        mix = MIX_SEPARATOR.join(sorted(ammo for ammo, _ in kill.hits))
        for ammo, hits in ((TOTAL_AMMO, sum(n for _, n in kill.hits)), *kill.hits):
            done, total = totals.get((kill.victim_type, mix, ammo), (0, 0))
            totals[(kill.victim_type, mix, ammo)] = (done + 1, total + hits)
    return totals


def _replace_aircraft_ammo(
    mission: Mission, kills: Iterable[SingleAttackerKill], objects: dict[str, GameObject]
) -> set[int]:
    """Rewrite the mission's `MissionAircraftAmmo` rows. Returns the aircraft ids whose level-2 rows may change: the
    types the mission had before and the ones it has now."""
    kills = tuple(kills)
    totals = aircraft_ammo_totals(kills)
    old_ids = set(MissionAircraftAmmo.objects.filter(mission=mission).values_list("aircraft_id", flat=True))
    MissionAircraftAmmo.objects.filter(mission=mission).delete()
    MissionAircraftAmmo.objects.bulk_create(
        MissionAircraftAmmo(mission=mission, aircraft=objects[victim], ammo=ammo, kills=n, hits=hits)
        for (victim, ammo), (n, hits) in sorted(totals.items())
    )
    mixes = aircraft_ammo_mix_totals(kills)
    old_ids |= set(MissionAircraftAmmoMix.objects.filter(mission=mission).values_list("aircraft_id", flat=True))
    MissionAircraftAmmoMix.objects.filter(mission=mission).delete()
    MissionAircraftAmmoMix.objects.bulk_create(
        MissionAircraftAmmoMix(mission=mission, aircraft=objects[victim], mix=mix, ammo=ammo, kills=n, hits=hits)
        for (victim, mix, ammo), (n, hits) in sorted(mixes.items())
    )
    return old_ids | {objects[victim].pk for victim, _ in totals} | {objects[victim].pk for victim, _, _ in mixes}
