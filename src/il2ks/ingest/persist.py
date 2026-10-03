"""MissionResult -> level-1 rows, plus the incremental level-2 update (FR-ING-5, FR-ING-6, FR-ING-9, TD-08).

Order inside `save_mission` (the caller holds the transaction):
1. upsert `Mission` by `(server_uid, mission_uid)`; if it existed, subtract its old contribution from level 2
2. register game objects and countries, upsert players
3. upsert sorties by `(mission, account_uuid, spawn_tick)` (PKs kept), delete sorties that no longer exist
4. replace PvP `Kill` rows, upsert `PlayerMission`, fill the mission counters
5. add the new contribution to level 2
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count

from il2ks.core.catalog.loader import Catalog
from il2ks.core.logparse.events import TICKS_PER_SECOND, Pos
from il2ks.core.replay.result import (
    AmmoCounts,
    Counterpart,
    DamageExchange,
    KillResult,
    MissionResult,
    SortieResult,
    TimelineEntry,
)
from il2ks.db.models import (
    Country,
    GameObject,
    Kill,
    Mission,
    Player,
    PlayerMission,
    PlayerSortie,
)
from il2ks.ingest.aggregates import add_mission, prune_player_aircraft, refresh_players, subtract_mission
from il2ks.ingest.counters import SORTIE_COUNTERS, clean_counters, counted_sorties

PAYLOAD_NAME_MAX = 128  # PlayerSortie.payload_name max_length
REDFOR = 1
BLUFOR = 2


@dataclass(frozen=True, slots=True)
class MissionMeta:
    """What `ingest` knows about a mission besides the replay result."""

    server_uid: uuid.UUID
    mission_uid: str  # file name timestamp
    started_at: datetime  # UTC, resolved from the local-time file name (TD-15)
    archive_path: str


def save_mission(result: MissionResult, meta: MissionMeta, catalog: Catalog) -> Mission:
    """Upsert one mission's level-1 rows by natural key and update level-2 totals incrementally.

    Must run inside the caller's `transaction.atomic()`. Safe to call again for the same mission (re-ingest,
    reprocess): the mission's old contribution is subtracted from level 2 first, rows that no longer exist are deleted,
    and PKs of rows that still exist are kept (FR-ING-9, FR-WEB-13).
    """
    clock = _Clock(meta.started_at)
    mission, created = Mission.objects.update_or_create(
        server_uid=meta.server_uid, mission_uid=meta.mission_uid, defaults=_mission_fields(result, meta, clock)
    )
    old_player_ids: set[int] = set()
    if not created:
        subtract_mission(mission, prune=False)
        old_player_ids = set(PlayerSortie.objects.filter(mission=mission).values_list("player_id", flat=True))

    objects = register_game_objects(_object_types(result), catalog)
    register_countries(result.mission.countries, catalog)
    players = _upsert_players(result.sorties, clock)
    sorties = _upsert_sorties(mission, result, clock, catalog, objects, players)
    _replace_kills(mission, result.kills, clock, sorties)
    _upsert_player_missions(mission, result.sorties, players)
    _update_mission_counters(mission)

    add_mission(mission)
    prune_player_aircraft(old_player_ids)
    refresh_players(old_player_ids - {p.pk for p in players.values()})
    return mission


# --- reference data ---


def register_game_objects(log_names: Iterable[str], catalog: Catalog) -> dict[str, GameObject]:
    """Get or create a `GameObject` per log name (FR-ING-7). Unknown types are stored with `is_known=False`.

    Existing rows: class, playable and known flags follow the catalog (so a catalog update fixes old unknowns), but the
    display name is only replaced while it still equals the log name, i.e. the auto-registered placeholder (TD-24:
    admins may edit names).
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
                is_playable=info.is_playable,
                is_known=info.is_known,
            )
            continue
        display_name = info.display_name if obj.display_name == obj.log_name and info.display_name else obj.display_name
        new = (display_name, info.cls, info.is_playable, info.is_known)
        if new != (obj.display_name, obj.cls, obj.is_playable, obj.is_known):
            obj.display_name, obj.cls, obj.is_playable, obj.is_known = new
            obj.save(update_fields=["display_name", "cls", "is_playable", "is_known"])
    return existing


def register_countries(countries: dict[int, int], catalog: Catalog) -> None:
    """Create missing `Country` rows from CNTRS with the plain coalition name (doc 06). Existing rows are left alone
    (admin-editable, FR-ADM-5)."""
    for code, coalition in sorted(countries.items()):
        Country.objects.get_or_create(
            code=code, defaults={"coalition": coalition, "display_name": catalog.coalition_name(coalition)}
        )


def _object_types(result: MissionResult) -> set[str]:
    types = set(result.object_types_seen) | set(result.unknown_object_types)
    types.update(s.aircraft_type for s in result.sorties)
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
    `players_total` counts every player with a sortie of any role."""
    counted = counted_sorties().filter(mission=mission)
    totals = clean_counters(
        counted.aggregate(**{k: SORTIE_COUNTERS[k] for k in ("sorties", "kills_air", "kills_ground")})
    )
    by_coalition = {row["coalition"]: row["n"] for row in counted.values("coalition").annotate(n=Count("pk"))}
    mission.players_total = PlayerSortie.objects.filter(mission=mission).values("player_id").distinct().count()
    mission.sorties_total = int(totals["sorties"])
    mission.redfor_sorties = by_coalition.get(REDFOR, 0)
    mission.blufor_sorties = by_coalition.get(BLUFOR, 0)
    mission.kills_air = int(totals["kills_air"])
    mission.kills_ground = int(totals["kills_ground"])
    mission.save(
        update_fields=[
            "players_total",
            "sorties_total",
            "redfor_sorties",
            "blufor_sorties",
            "kills_air",
            "kills_ground",
        ]
    )


# --- players ---


def _upsert_players(sorties: Iterable[SortieResult], clock: _Clock) -> dict[str, Player]:
    """Get or create a `Player` per account UUID. Identity fields are recomputed later by `refresh_players`."""
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
    """One row per player with a sortie of any role; counters come from counted sorties. Coalition = first sortie's."""
    coalition: dict[int, int] = {}
    for s in sorted(sorties, key=lambda s: (s.spawn_tick, s.index)):
        coalition.setdefault(players[s.account_uuid].pk, s.coalition)
    counters = {
        row["player_id"]: clean_counters(row)
        for row in counted_sorties().filter(mission=mission).values("player_id").annotate(**SORTIE_COUNTERS)
    }
    for player_id, side in coalition.items():
        values = counters.get(player_id) or clean_counters({})
        PlayerMission.objects.update_or_create(
            player_id=player_id, mission=mission, defaults={"coalition": side, **values}
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
        _fill_sortie(row, s, clock, catalog, objects[s.aircraft_type], players[s.account_uuid])
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
    row: PlayerSortie, s: SortieResult, clock: _Clock, catalog: Catalog, aircraft: GameObject, player: Player
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
    row.ammo = _ammo_json(s)
    row.pos_spawn_x, row.pos_spawn_y, row.pos_spawn_z = s.spawn_pos


# --- JSON fields (plain dicts and lists, doc 06) ---


def _counts_json(c: AmmoCounts) -> dict[str, int]:
    return {"bullets": c.bullets, "shells": c.shells, "bombs": c.bombs, "rockets": c.rockets}


def _ammo_json(s: SortieResult) -> dict[str, object]:
    return {
        "loaded": _counts_json(s.ammo_loaded),
        "left": None if s.ammo_left is None else _counts_json(s.ammo_left),
        "hits": [{"ammo": h.ammo, "hits_given": h.hits_given, "hits_received": h.hits_received} for h in s.ammo_hits],
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


def _replace_kills(
    mission: Mission, kills: Iterable[KillResult], clock: _Clock, sorties: dict[int, PlayerSortie]
) -> None:
    """`Kill` is PvP only (doc 06): rows where both killer and victim are player sorties.

    Upserted by `(killer_sortie, victim_sortie, tick, credit)` so re-ingesting keeps PKs; rows not in the result are
    deleted."""
    existing = {
        (k.killer_sortie_id, k.victim_sortie_id, k.tick, k.credit): k for k in Kill.objects.filter(mission=mission)
    }
    changed: list[Kill] = []
    new: list[Kill] = []
    for k in kills:
        if k.killer_sortie_index is None or k.victim_sortie_index is None:
            continue
        killer, victim = sorties[k.killer_sortie_index], sorties[k.victim_sortie_index]
        row = existing.pop((killer.pk, victim.pk, k.tick, k.credit), None)
        if row is None:
            row = Kill(mission=mission, killer_sortie=killer, victim_sortie=victim, tick=k.tick, credit=k.credit)
            new.append(row)
        else:
            changed.append(row)
        pos = _pos_json(k.pos)
        row.time = clock.at(k.tick)
        row.is_friendly = k.is_friendly
        row.via = k.via
        row.pos_x, row.pos_y, row.pos_z = (None, None, None) if pos is None else pos
    Kill.objects.filter(pk__in=[k.pk for k in existing.values()]).delete()
    Kill.objects.bulk_update(changed, ["time", "is_friendly", "via", "pos_x", "pos_y", "pos_z"])
    Kill.objects.bulk_create(new)
