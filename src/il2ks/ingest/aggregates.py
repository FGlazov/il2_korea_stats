"""Level-2 aggregates: Player totals, PlayerName, PlayerAircraft (TD-08). All aggregation happens in `ingest`.

Level 2 is always recomputed from level 1, never adjusted by deltas: `recompute_players` rebuilds, for the players it
gets, their totals from their `PlayerMission` rows, their `PlayerAircraft` rows from their counted `PlayerSortie` rows,
and their identity fields from all their sorties. `save_mission` calls it for the players a mission touched (the old
and the new ones) and `rebuild_aggregates` for every player. It is the one code path, so incremental == rebuild by
construction. The counter list lives in `ingest.counters` so all paths use one definition (TD-16).

Players are handled in chunks: a few grouped queries per chunk, then writes only for rows whose values changed.

Tours (TD-26): `PlayerTour` (sum of the player's `PlayerMission` rows per `Mission.tour`) and `PlayerTourAircraft`
(counted sorties per tour and aircraft) are recomputed the same way. `save_mission` passes the tours it touched (the
mission's new and old tour), so the per-tour part of a recompute reads only those tours' rows; `rebuild_aggregates`
recomputes every tour. Server activity per day (`ingest.activity`) is rebuilt for all days. Elo stays all-time (it
replays all kills, `ingest.ratings`).

Cost: the all-time part of a player's recompute reads all of that player's level-1 rows, so it grows with their history.
The per-tour part is bounded by the tour.

Killboard pairs (`ingest.pairs`, FR-WEB-9) and ironman streaks (`ingest.streaks`, FR-WEB-23) are recomputed per chunk
too; a pair touching the chunk is rewritten in both mirror directions, so the opponent need not be in the chunk.

Identity fields (`Player.first_seen`, `last_seen`, `current_name`, `name_lower` and the `PlayerName` history) aren't
summed: they come from the player's sorties. A player without sorties keeps the identity values they had.
"""

from collections.abc import Iterable
from datetime import datetime

from django.db import models
from django.db.models import Max, Min, Sum

from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.core.tours import TourRules
from il2ks.db.models import (
    AircraftAmmoStats,
    MissionAircraftAmmo,
    Player,
    PlayerAircraft,
    PlayerMission,
    PlayerName,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
)
from il2ks.db.site import bump_data_version
from il2ks.ingest.activity import rebuild_activity
from il2ks.ingest.aircraft_stats import rebuild_aircraft_stats
from il2ks.ingest.counters import COUNTER_FIELDS, SORTIE_COUNTERS, CounterValues, clean_counters, counted_sorties
from il2ks.ingest.pairs import recompute_killboard
from il2ks.ingest.ratings import recompute_ratings
from il2ks.ingest.scoring import rebuild_sortie_scores
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.streaks import recompute_streaks
from il2ks.ingest.tours import assign_missing, retour

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit


def recompute_players(player_ids: Iterable[int], tour_ids: Iterable[int] | None = None) -> None:
    """Recompute level 2 for these players from level 1 (TD-08, FR-ING-9).

    `tour_ids` limits the per-tour rows to these tours (None = all tours of these players, as a rebuild does); the
    all-time rows are always recomputed.

    - `Player` counters = sum of the player's `PlayerMission` rows (zero when there are none).
    - `PlayerAircraft` = counted sorties grouped by aircraft, upserted by `(player, aircraft)` so existing PKs stay;
      rows without counted sorties are deleted.
    - `PlayerTour` = the sum of the player's `PlayerMission` rows per tour; `PlayerTourAircraft` = counted sorties per
      tour and aircraft. Rows without anything left are deleted.
    - Identity fields and the `PlayerName` history from all sorties of any role (`_refresh_identity`).
    Players are never deleted."""
    ids = sorted(set(player_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        _recompute_totals(chunk)
        _recompute_aircraft(chunk)
        _recompute_tours(chunk, tours)
        _refresh_identity(chunk)
        recompute_killboard(chunk)  # level-2 pair rows, ingest.pairs (FR-WEB-9)
        recompute_streaks(chunk)  # ironman streaks, ingest.streaks (FR-WEB-23)


def rebuild_aggregates(
    ratings: RatingRules = DEFAULT_RULES,
    tours: TourRules | None = None,
    *,
    reassign_tours: bool = False,
    marks: MarkRules = DEFAULT_MARK_RULES,
    score: ScoreRules = DEFAULT_SCORE_RULES,
) -> None:
    """Recompute every level-2 row from level 1 (`il2ks rebuild-aggregates`): the sortie scores under the `[score]`
    rules (`score`: how a changed score config takes effect, no reprocess), the `PlayerMission` counters that sum
    them, `recompute_players` for all players (all tours), the hits to destroy per aircraft type
    (`recompute_aircraft_ammo`), then the Elo ratings (`recompute_ratings`, which replays all kills) and the stat
    thresholds (FR-WEB-22).

    `tours` (the `[tours]` rules) first gives a tour to missions that have none (a database from before tours existed).
    With `reassign_tours` it moves every mission to the tour it belongs to under these rules (`--retour`, after a mode,
    start or timezone change)."""
    if tours is not None:
        if reassign_tours:
            retour(tours)
        else:
            assign_missing(tours)
    if rebuild_sortie_scores(score):
        refresh_player_missions()
    recompute_players(Player.objects.values_list("pk", flat=True))
    recompute_aircraft_ammo(
        set(MissionAircraftAmmo.objects.values_list("aircraft_id", flat=True))
        | set(AircraftAmmoStats.objects.values_list("aircraft_id", flat=True))
    )
    rebuild_aircraft_stats()
    rebuild_activity()
    recompute_ratings(ratings)
    recompute_thresholds(marks)
    bump_data_version()  # TD-28: pages changed


def recompute_aircraft_ammo(aircraft_ids: Iterable[int]) -> None:
    """Hits to destroy (FR-WEB-18): `AircraftAmmoStats` for these victim aircraft types = the sum of their
    `MissionAircraftAmmo` rows over all missions, upserted by `(aircraft, ammo)`; rows with nothing left are deleted.
    Cheap enough to run for every type (a few dozen rows per mission), so `save_mission` and the rebuild share it."""
    ids = sorted(set(aircraft_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        wanted = {
            (row["aircraft_id"], row["ammo"]): (row["kills"], row["hits"])
            for row in MissionAircraftAmmo.objects.filter(aircraft_id__in=chunk)
            .values("aircraft_id", "ammo")
            .annotate(kills=Sum("kills"), hits=Sum("hits"))
        }
        existing = {(r.aircraft_id, r.ammo): r for r in AircraftAmmoStats.objects.filter(aircraft_id__in=chunk)}
        changed: list[AircraftAmmoStats] = []
        new: list[AircraftAmmoStats] = []
        for key, (kills, hits) in wanted.items():
            row = existing.pop(key, None)
            if row is None:
                new.append(AircraftAmmoStats(aircraft_id=key[0], ammo=key[1], kills=kills, hits=hits))
            elif (row.kills, row.hits) != (kills, hits):
                row.kills, row.hits = kills, hits
                changed.append(row)
        AircraftAmmoStats.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
        AircraftAmmoStats.objects.bulk_update(changed, ["kills", "hits"])
        AircraftAmmoStats.objects.bulk_create(new)


def refresh_player_missions() -> None:
    """Rewrite the counters of every existing `PlayerMission` row from its counted sorties (after a rescoring). Only
    rows with a differing value are written; none are created or deleted (`save_mission` owns that)."""
    wanted = {
        (row["player_id"], row["mission_id"]): clean_counters(row)
        for row in counted_sorties().values("player_id", "mission_id").annotate(**SORTIE_COUNTERS)
    }
    changed: list[PlayerMission] = []
    for row in PlayerMission.objects.all().iterator():
        values = wanted.get((row.player_id, row.mission_id))
        if values is not None and _assign(row, values):
            changed.append(row)
    PlayerMission.objects.bulk_update(changed, list(COUNTER_FIELDS), batch_size=200)


def _recompute_totals(chunk: list[int]) -> None:
    sums = {n: Sum(n) for n in COUNTER_FIELDS}
    totals = {
        row["player_id"]: row
        for row in PlayerMission.objects.filter(player_id__in=chunk).values("player_id").annotate(**sums)
    }
    changed: list[Player] = []
    for player in Player.objects.filter(pk__in=chunk):
        values = clean_counters(totals.get(player.pk, {}))
        if _assign(player, values):
            changed.append(player)
    Player.objects.bulk_update(changed, list(COUNTER_FIELDS))


def _recompute_aircraft(chunk: list[int]) -> None:
    wanted = {
        (row["player_id"], row["aircraft_id"]): clean_counters(row)
        for row in counted_sorties()
        .filter(player_id__in=chunk)
        .values("player_id", "aircraft_id")
        .annotate(**SORTIE_COUNTERS)
    }
    _sync(PlayerAircraft, ("player_id", "aircraft_id"), wanted, PlayerAircraft.objects.filter(player_id__in=chunk))


def _recompute_tours(chunk: list[int], tour_ids: list[int] | None) -> None:
    """`PlayerTour` and `PlayerTourAircraft` for these players, limited to `tour_ids` unless that is None."""
    missions = PlayerMission.objects.filter(player_id__in=chunk, mission__tour__isnull=False)
    sorties = counted_sorties().filter(player_id__in=chunk, mission__tour__isnull=False)
    totals = PlayerTour.objects.filter(player_id__in=chunk)
    aircraft = PlayerTourAircraft.objects.filter(player_id__in=chunk)
    if tour_ids is not None:
        missions = missions.filter(mission__tour_id__in=tour_ids)
        sorties = sorties.filter(mission__tour_id__in=tour_ids)
        totals = totals.filter(tour_id__in=tour_ids)
        aircraft = aircraft.filter(tour_id__in=tour_ids)
    sums = {n: Sum(n) for n in COUNTER_FIELDS}
    wanted_totals = {
        (row["player_id"], row["mission__tour_id"]): clean_counters(row)
        for row in missions.values("player_id", "mission__tour_id").annotate(**sums)
    }
    _sync(PlayerTour, ("player_id", "tour_id"), wanted_totals, totals)
    wanted_aircraft = {
        (row["player_id"], row["mission__tour_id"], row["aircraft_id"]): clean_counters(row)
        for row in sorties.values("player_id", "mission__tour_id", "aircraft_id").annotate(**SORTIE_COUNTERS)
    }
    _sync(PlayerTourAircraft, ("player_id", "tour_id", "aircraft_id"), wanted_aircraft, aircraft)


def _sync[M: models.Model](
    model: type[M],
    key_fields: tuple[str, ...],
    wanted: dict[tuple[int, ...], CounterValues],
    existing_rows: models.QuerySet[M],
) -> None:
    """Make the counter rows in `existing_rows` equal `wanted` (keyed by `key_fields`): insert missing rows, update
    changed values only, delete rows that are no longer wanted. Existing PKs are kept."""
    existing = {tuple(getattr(r, f) for f in key_fields): r for r in existing_rows}
    changed: list[M] = []
    new: list[M] = []
    for key, values in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(model(**dict(zip(key_fields, key, strict=True)), **values))
        elif _assign(row, values):
            changed.append(row)
    model._default_manager.filter(pk__in=[r.pk for r in existing.values()]).delete()  # nothing counted left
    model._default_manager.bulk_update(changed, list(COUNTER_FIELDS))
    model._default_manager.bulk_create(new)


def _assign(row: models.Model, values: CounterValues) -> bool:
    """Set the counter fields on `row`; True if any value changed."""
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def _refresh_identity(chunk: list[int]) -> None:
    """Identity fields and nickname history from the players' sorties (any role).

    `first_seen` = earliest spawn, `last_seen` = latest sortie end, `current_name` = name of the latest spawn (one
    account never spawns twice at the same instant: the sortie key has a unique tick per mission). A player without
    sorties keeps the values they had (their row and URL stay, FR-WEB-13). `PlayerName` = one row per distinct name."""
    groups = (
        PlayerSortie.objects.filter(player_id__in=chunk)
        .values("player_id", "name_at_time")
        .annotate(first=Min("spawned_at"), last=Max("ended_at"), spawned=Max("spawned_at"))
    )
    by_player: dict[int, dict[str, tuple[datetime, datetime, datetime]]] = {}
    for row in groups:
        by_player.setdefault(row["player_id"], {})[row["name_at_time"]] = (row["first"], row["last"], row["spawned"])

    players = Player.objects.in_bulk(by_player)
    names = {(n.player_id, n.name): n for n in PlayerName.objects.filter(player_id__in=chunk)}
    changed_players: list[Player] = []
    changed_names: list[PlayerName] = []
    new_names: list[PlayerName] = []
    for player_id, seen in by_player.items():
        player = players[player_id]
        latest = max(seen, key=lambda name: seen[name][2])
        first_seen = min(first for first, _, _ in seen.values())
        last_seen = max(last for _, last, _ in seen.values())
        identity = (latest, latest.lower(), first_seen, last_seen)
        if identity != (player.current_name, player.name_lower, player.first_seen, player.last_seen):
            player.current_name, player.name_lower, player.first_seen, player.last_seen = identity
            changed_players.append(player)
        for name, (first, last, _) in seen.items():
            row = names.pop((player_id, name), None)
            if row is None:
                new_names.append(
                    PlayerName(
                        player_id=player_id, name=name, name_lower=name.lower(), first_seen=first, last_seen=last
                    )
                )
            elif (row.first_seen, row.last_seen) != (first, last):
                row.first_seen, row.last_seen = first, last
                changed_names.append(row)
    # Names left over belong to players with sorties that no longer use them; players without sorties keep theirs.
    PlayerName.objects.filter(pk__in=[n.pk for (pid, _), n in names.items() if pid in by_player]).delete()
    Player.objects.bulk_update(changed_players, ["current_name", "name_lower", "first_seen", "last_seen"])
    PlayerName.objects.bulk_update(changed_names, ["first_seen", "last_seen"])
    PlayerName.objects.bulk_create(new_names)
