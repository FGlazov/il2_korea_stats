"""Level-2 aggregates: Player totals, PlayerName, PlayerAircraft (TD-08). All aggregation happens in `ingest`.

Level 2 is always recomputed from level 1, never adjusted by deltas: `recompute_players` rebuilds, for the players it
gets, their totals from their `PlayerMission` rows, their `PlayerAircraft` rows from their counted `PlayerSortie` rows,
and their identity fields from all their sorties. `refresh_tours` calls it for the players of the tours a save touched
(`rebuild_aggregates`: every player): the one code path (doc 14), so incremental == rebuild by
construction. The counter list lives in `ingest.counters` so all paths use one definition (TD-16).

Players are handled in chunks: a few grouped queries per chunk, then writes only for rows whose values changed.

Tours (TD-26): `PlayerTour` (sum of the player's `PlayerMission` rows per `Mission.tour`) and `PlayerTourAircraft`
(counted sorties per tour and aircraft) are recomputed the same way. `refresh_tours` gets the tours a save touched (the
mission's new and old tour), so the per-tour part of a recompute reads only those tours' rows; `rebuild_aggregates`
recomputes every tour. Server activity per day (`ingest.activity`) is rebuilt for all days. Elo stays all-time (it
replays all kills, `ingest.ratings`).

Cost: the all-time part of a player's recompute reads all of that player's level-1 rows, so it grows with their history.
The per-tour part is bounded by the tour.

Killboard pairs (`ingest.pairs`, FR-WEB-9) and ironman streaks (`ingest.streaks`, FR-WEB-23) are recomputed per chunk
too, all-time and per tour (the same tour limit as above); a pair touching the chunk is rewritten in both mirror
directions, so the opponent need not be in the chunk.

Identity fields (`Player.first_seen`, `last_seen`, `current_name`, `name_lower` and the `PlayerName` history) aren't
summed: they come from the player's sorties. A player without sorties keeps the identity values they had.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from django.db import models
from django.db.models import Max, Min, Q, QuerySet, Sum
from django.db.models.functions import TruncDate

from il2ks.core.killboard import DEFAULT_KILLBOARD_RULES, KillboardRules
from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.core.tours import TourRules
from il2ks.db.models import (
    ActivityDay,
    AircraftAmmoMixStats,
    AircraftAmmoStats,
    AircraftMatchup,
    Mission,
    MissionAircraftAmmo,
    MissionAircraftAmmoMix,
    Player,
    PlayerAircraft,
    PlayerMission,
    PlayerName,
    PlayerPool,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
    PlayerTourPool,
    Propulsion,
    Tour,
    TourAircraftStats,
)
from il2ks.db.site import bump_data_version, clear_level2_pending, get_site_settings
from il2ks.ingest.achievements import (
    adopt_wanted_rules,
    applied_rules,
    recompute_holders,
    refresh_achievement_tours,
    rollup_achievements,
)
from il2ks.ingest.activity import rebuild_activity, recompute_days
from il2ks.ingest.aircraft_mods import scopes_of, significant_mods
from il2ks.ingest.aircraft_stats import (
    matchup_kills,
    rebuild_aircraft_stats,
    recompute_aircraft_stats,
    recompute_matchups,
    recompute_payload_elo,
)
from il2ks.ingest.builds import recompute_builds
from il2ks.ingest.counters import COUNTER_FIELDS, SORTIE_COUNTERS, CounterValues, clean_counters, counted_sorties
from il2ks.ingest.dbutil import update_rows
from il2ks.ingest.pairs import recompute_killboard
from il2ks.ingest.ratings import recompute_ratings
from il2ks.ingest.scoring import rebuild_sortie_scores
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.streaks import refresh_streak_tours, rollup_streaks
from il2ks.ingest.tours import assign_missing, retour
from il2ks.ingest.type_board import recompute_type_killboard

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit


def recompute_players(
    player_ids: Iterable[int], tour_ids: Iterable[int] | None = None, ratings: RatingRules | None = None
) -> None:
    """Recompute level 2 for these players from level 1 (TD-08, FR-ING-9), in the per-tour order of doc 14:

    1. `recompute_player_rows`: everything that level 1 gives directly (below);
    2. with `ratings`, the Elo replay of the tours (`recompute_ratings`, which also writes the sorties' `elo_peak`; the
       loadouts' average Elo is the caller's, after the aircraft rows exist: `recompute_payload_elo`);
    3. the streaks and the medals of each tour (the Top Rated medal reads the peaks of step 2), then the all-time rows
       rolled up from them (`refresh_streak_tours` / `rollup_streaks`, `refresh_achievement_tours` /
       `rollup_achievements`; cumulative medals sum the `PlayerTour` counters of step 1).

    `tour_ids` limits the per-tour rows to these tours (None = all tours of these players, as a rebuild does); the
    all-time rows are always recomputed. Without `ratings` the replay is skipped (a caller that does it once afterwards,
    or no ratings at all: a live pass, FR-ING-15)."""
    ids = sorted(set(player_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    recompute_player_rows(ids, tours)
    if ratings is not None:
        recompute_ratings(ratings, tours, payload_elo=False)
    rules = applied_rules().active()
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        refresh_streak_tours(chunk, tours)  # ironman streaks per tour, ingest.streaks (FR-WEB-23)
        refresh_achievement_tours(chunk, tours, rules)  # medals per tour, ingest.achievements (FR-WEB-26)
        rollup_streaks(chunk)  # the all-time rows: the best of the tours, ...
        rollup_achievements(chunk, rules)  # ... the highest tier of the tours, cumulative medals summed over them


def recompute_player_rows(player_ids: Iterable[int], tour_ids: Iterable[int] | None = None) -> None:
    """The part of `recompute_players` that level 1 gives directly, no Elo needed (`tour_ids` as there):

    - `Player` counters = sum of the player's `PlayerMission` rows (zero when there are none).
    - `PlayerAircraft` = counted sorties grouped by aircraft, upserted by `(player, aircraft)` so existing PKs stay;
      rows without counted sorties are deleted.
    - `PlayerTour` = the sum of the player's `PlayerMission` rows per tour; `PlayerTourAircraft` = counted sorties per
      tour and aircraft. Rows without anything left are deleted.
    - `PlayerPool` / `PlayerTourPool` = the same grouped by the aircraft's propulsion (prop / jet) instead of the type.
    - The favourite loadout rows, the identity fields and the `PlayerName` history from all sorties of any role
      (`_refresh_identity`), the killboard rows (`ingest.pairs`, `ingest.type_board`).
    Players are never deleted."""
    ids = sorted(set(player_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        _recompute_totals(chunk)
        _recompute_aircraft(chunk)
        _recompute_pools(chunk)
        _recompute_tours(chunk, tours)
        recompute_builds(chunk, tours)  # favourite loadout, weapon mods, gun ammo mix per aircraft (ingest.builds)
        _refresh_identity(chunk)
        recompute_killboard(chunk, tours)  # level-2 pair rows, all-time and per tour, ingest.pairs (FR-WEB-9)
        recompute_type_killboard(chunk, tours)  # ... and by enemy aircraft type, ingest.type_board (FR-WEB-9)


def rebuild_aggregates(
    ratings: RatingRules = DEFAULT_RULES,
    tours: TourRules | None = None,
    *,
    reassign_tours: bool = False,
    marks: MarkRules = DEFAULT_MARK_RULES,
    score: ScoreRules = DEFAULT_SCORE_RULES,
    board: KillboardRules = DEFAULT_KILLBOARD_RULES,
) -> None:
    """Recompute every level-2 row from level 1 (`il2ks rebuild-aggregates`): the sortie scores under the `[score]`
    rules (`score`: how a changed score config takes effect, no reprocess), the `PlayerMission` counters that sum
    them, then `refresh_tours(None, ratings)` (every tour: the player rows, each tour's Elo replay, each tour's
    streaks and medals and the all-time roll-ups, the aircraft types, the hits to destroy), the medal holder counts and
    the stat thresholds (FR-WEB-22).

    `tours` (the `[tours]` rules) first gives a tour to missions that have none (a database from before tours existed).
    With `reassign_tours` it moves every mission to the tour it belongs to under these rules (`--retour`, after a mode,
    start or timezone change).

    `board` (the `[killboard]` rules) is stored first (`SiteSettings.killboard_assists`): the killboard rows follow it,
    here and in the incremental updates until the next rebuild."""
    _store_board_rules(board)
    adopt_wanted_rules()  # the achievement rows below are computed with the admin's current thresholds and switches
    if tours is not None:
        if reassign_tours:
            retour(tours)
        else:
            assign_missing(tours)
    rebuild_sortie_scores(score)
    refresh_player_missions()  # also after a level-1 column was filled by a backfill (the interception counters)
    refresh_tours(None, ratings)  # every tour, and whatever has none: rows, Elo replay, streaks and medals, roll-ups
    recompute_holders()  # once, after the medal rows are final
    recompute_thresholds(marks)
    clear_level2_pending()  # everything was recomputed: whatever a killed batch left behind is repaired
    bump_data_version()  # TD-28: pages changed


def refresh_tours(tour_ids: Iterable[int] | None, ratings: RatingRules | None, *, payload_elo: bool = True) -> None:
    """The one level-2 path (doc 14 "Level-2 refresh"): a full refresh of these tours from level 1, with no record of
    what a save changed. Every mission save, batch pass, live pass and discard calls it with the tours it touched, and
    `rebuild_aggregates` with None (every tour and everything that has none).

    The entities to refresh are found by queries over the tours' level-1 rows, plus the rows level 2 holds for the
    tours (so a player, type, pair or day that dropped out of a re-ingested mission is corrected, too):
    - the players with a sortie or a `PlayerMission` / `PlayerTour` row in the tours,
    - the aircraft types flown in them (counted sorties, `PlayerTourAircraft`, `TourAircraftStats` rows) and the types
      with ammo rows (hits to destroy: every type that has some, they are not per tour),
    - the (killer type, victim type) pairs of their counted kills (and the tours' `AircraftMatchup` rows),
    - the UTC days of their missions, and the activity rows within the tours' time ranges (a mission that a re-ingest
      moved leaves its old day behind there).

    Two steps, in this order: `_recompute_tour_scope` and `_recompute_all_time`. The first is, per tour: the rows
    derived from level 1, the Elo replay of the tour alone (`ratings`; None skips it: a live pass, or a caller that
    replays everything once), then the tour's streaks and medals (which read the replay's `elo_peak`), then the
    all-time rows rolled up from the tours' (`recompute_players`), then the aircraft types of the tours. Neither step
    runs the medal holder counts or the thresholds: the caller does, once, in that order.

    `payload_elo`: the loadouts' average pilot Elo (`recompute_payload_elo`, after the aircraft rows exist); a caller
    that runs several passes in a row asks for it on the last one only."""
    if tour_ids is None:
        _recompute_everything(ratings)
    else:
        tours = sorted(set(tour_ids))
        if tours:  # none left (a batch's last pass ran at its last 10% line): still the payload Elo below
            scope = _tour_scope(tours)
            _recompute_tour_scope(scope, ratings)
            _recompute_all_time(scope)
    if payload_elo:
        recompute_payload_elo()


@dataclass(frozen=True, slots=True)
class _TourScope:
    tours: list[int]
    players: list[int]
    aircraft: list[int]
    ammo_aircraft: list[int]
    pairs: list[tuple[int, int]]
    days: list[date]


def _tour_scope(tours: list[int]) -> _TourScope:
    """What `refresh_tours` has to recompute for these tours, by queries (never by tracking)."""
    in_tours = {"mission__tour_id__in": tours}
    players = set(PlayerSortie.objects.filter(**in_tours).values_list("player_id", flat=True).distinct())
    players |= set(PlayerMission.objects.filter(**in_tours).values_list("player_id", flat=True).distinct())
    players |= set(PlayerTour.objects.filter(tour_id__in=tours).values_list("player_id", flat=True).distinct())
    flown = set(counted_sorties().filter(**in_tours).values_list("aircraft_id", flat=True).distinct())
    flown |= set(PlayerTourAircraft.objects.filter(tour_id__in=tours).values_list("aircraft_id", flat=True).distinct())
    flown |= set(TourAircraftStats.objects.filter(tour_id__in=tours).values_list("aircraft_id", flat=True).distinct())
    ammo = set(MissionAircraftAmmo.objects.filter(**in_tours).values_list("aircraft_id", flat=True).distinct())
    ammo |= set(MissionAircraftAmmoMix.objects.filter(**in_tours).values_list("aircraft_id", flat=True).distinct())
    # the all-time ammo rows are not per tour: those of every type that has some are re-summed, so a type a re-ingested
    # mission no longer has loses its share (one grouped query per chunk, a few dozen rows per type)
    ammo |= set(AircraftAmmoStats.objects.values_list("aircraft_id", flat=True).distinct())
    ammo |= set(AircraftAmmoMixStats.objects.values_list("aircraft_id", flat=True).distinct())
    pairs = set(
        matchup_kills()
        .filter(**in_tours)
        .values_list("killer_sortie__aircraft_id", "victim_sortie__aircraft_id")
        .distinct()
    )
    pairs |= set(
        AircraftMatchup.objects.filter(tour_id__in=tours).values_list("killer_aircraft_id", "victim_aircraft_id")
    )
    days = set(
        Mission.objects.filter(tour_id__in=tours)
        .annotate(day=TruncDate("started_at", tzinfo=UTC))
        .values_list("day", flat=True)
        .distinct()
    )
    days |= _recorded_days(tours)
    return _TourScope(
        tours=tours,
        players=sorted(players),
        aircraft=sorted(flown),
        ammo_aircraft=sorted(ammo | flown),
        pairs=sorted(pairs),
        days=sorted(days),
    )


def _recorded_days(tours: list[int]) -> set[date]:
    """The `ActivityDay` rows that lie within these tours' time ranges. A mission that a re-ingest moved to another
    start leaves its old day behind in the old tour's range, where no mission may be left to find it by."""
    in_range = Q(pk__in=[])
    for start, end in Tour.objects.filter(pk__in=tours).values_list("started_at", "ended_at"):
        in_range |= Q(day__gte=start.astimezone(UTC).date()) & (
            Q() if end is None else Q(day__lte=end.astimezone(UTC).date())
        )
    return set(ActivityDay.objects.filter(in_range).values_list("day", flat=True))


def _recompute_tour_scope(scope: _TourScope, ratings: RatingRules | None) -> None:
    """The per-tour step: the players' and types' rows of these tours (`PlayerTour`, `TourAircraftStats`, the
    per-tour killboards, matchups, ...), the tours' Elo, streaks and medals and the players' all-time streaks, medals
    and Elo rolled up from them (`recompute_players`). The other all-time rows are still written by the same recompute
    functions in the same pass; `_recompute_all_time` marks where they will be summed from these rows instead."""
    recompute_players(scope.players, scope.tours, ratings)
    recompute_aircraft_stats(scope.aircraft, scope.tours)
    recompute_matchups(scope.pairs)


def _recompute_all_time(scope: _TourScope) -> None:
    """The all-time step, for the entities of the tour step: ammo rows (they are not per tour) and the activity days.
    The all-time player and type rows are written by `_recompute_tour_scope` today (see there)."""
    recompute_aircraft_ammo(scope.ammo_aircraft)
    recompute_days(scope.days)


def _recompute_everything(ratings: RatingRules | None) -> None:
    """`refresh_tours(None)`: every player, type, pair and day, whatever tour they belong to (a rebuild)."""
    recompute_players(Player.objects.values_list("pk", flat=True), None, ratings)
    recompute_aircraft_ammo(
        set(MissionAircraftAmmo.objects.values_list("aircraft_id", flat=True))
        | set(AircraftAmmoStats.objects.values_list("aircraft_id", flat=True))
        | set(MissionAircraftAmmoMix.objects.values_list("aircraft_id", flat=True))
        | set(AircraftAmmoMixStats.objects.values_list("aircraft_id", flat=True))
    )
    rebuild_aircraft_stats()
    rebuild_activity()


def _store_board_rules(board: KillboardRules) -> None:
    settings = get_site_settings()
    if settings.killboard_assists != board.assists:
        settings.killboard_assists = board.assists
        settings.save(update_fields=["killboard_assists"])


type _AmmoKey = tuple[int, int | None, str, str, str, str]  # aircraft, tour, role, pattern, mix ('' = per ammo), ammo


def recompute_aircraft_ammo(aircraft_ids: Iterable[int]) -> None:
    """Hits to destroy (FR-WEB-18): `AircraftAmmoStats` and `AircraftAmmoMixStats` for these victim aircraft types =
    the sums of their `MissionAircraftAmmo` / `MissionAircraftAmmoMix` rows over the missions of every scope (all time
    and each tour, every role and each combat role, no filter and each modification pattern of the types with
    significant mods: the role and mods are those of the destroyed aircraft's sortie, a victim that was not a player
    sortie counts for `all` roles without a filter only); rows with nothing left are deleted. Cheap enough to run for
    every type (a few dozen rows per mission), so `save_mission` and the rebuild share it."""
    ids = sorted(set(aircraft_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        significant = significant_mods(chunk)
        singles = _scoped_ammo(
            MissionAircraftAmmo.objects.filter(aircraft_id__in=chunk)
            .values("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "ammo")
            .annotate(n=Sum("kills"), h=Sum("hits")),
            significant,
            None,
        )
        _sync_ammo(AircraftAmmoStats, AircraftAmmoStats.objects.filter(aircraft_id__in=chunk), singles, "")
        mixes = _scoped_ammo(
            MissionAircraftAmmoMix.objects.filter(aircraft_id__in=chunk)
            .values("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "mix", "ammo")
            .annotate(n=Sum("kills"), h=Sum("hits")),
            significant,
            "mix",
        )
        _sync_ammo(AircraftAmmoMixStats, AircraftAmmoMixStats.objects.filter(aircraft_id__in=chunk), mixes, "mix")


def _scoped_ammo(
    rows: Iterable[Mapping[str, object]], significant: dict[int, tuple[int, ...]], mix_field: str | None
) -> dict[_AmmoKey, tuple[int, int]]:
    """The (kills, hits) of every scope: the level-1 rows summed per (type, tour, role, WM, mix, ammo), each added to
    every scope its destroyed aircraft's sortie belongs to (`aircraft_mods.scopes_of`)."""
    wanted: dict[_AmmoKey, tuple[int, int]] = {}
    for row in rows:
        aircraft, tour_id = int(str(row["aircraft_id"])), row["mission__tour_id"]
        mix = str(row[mix_field]) if mix_field else ""
        scopes = scopes_of(
            None if tour_id is None else int(str(tour_id)),
            str(row["combat_role"]),
            int(str(row["weapon_mods"])),
            significant.get(aircraft, ()),
        )
        for tour, role, pattern in scopes:
            key = (aircraft, tour, role, pattern, mix, str(row["ammo"]))
            kills, hits = wanted.get(key, (0, 0))
            wanted[key] = (kills + int(str(row["n"])), hits + int(str(row["h"])))
    return wanted


def _sync_ammo[M: models.Model](
    model: type[M], existing_rows: QuerySet[M], wanted: dict[_AmmoKey, tuple[int, int]], mix_field: str
) -> None:
    """Make the scoped hits-to-destroy rows equal `wanted` (new created, changed updated, the others deleted)."""
    existing = {
        (
            getattr(r, "aircraft_id"),  # noqa: B009
            getattr(r, "tour_id"),  # noqa: B009
            getattr(r, "role"),  # noqa: B009
            getattr(r, "mod_pattern"),  # noqa: B009
            getattr(r, mix_field) if mix_field else "",
            getattr(r, "ammo"),  # noqa: B009
        ): r
        for r in existing_rows
    }
    changed: list[M] = []
    new: list[M] = []
    for key, (kills, hits) in sorted(wanted.items(), key=lambda item: (item[0][0], item[0][1] or 0, *item[0][2:])):
        row = existing.pop(key, None)
        if row is None:
            extra = {mix_field: key[4]} if mix_field else {}
            new.append(
                model(
                    aircraft_id=key[0],
                    tour_id=key[1],
                    role=key[2],
                    mod_pattern=key[3],
                    ammo=key[5],
                    kills=kills,
                    hits=hits,
                    **extra,
                )
            )
        elif (getattr(row, "kills"), getattr(row, "hits")) != (kills, hits):  # noqa: B009
            setattr(row, "kills", kills)  # noqa: B010
            setattr(row, "hits", hits)  # noqa: B010
            changed.append(row)
    model.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(model, changed, ["kills", "hits"])
    model.objects.bulk_create(new)


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
    update_rows(PlayerMission, changed, list(COUNTER_FIELDS))


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
    update_rows(Player, changed, list(COUNTER_FIELDS))


def _recompute_aircraft(chunk: list[int]) -> None:
    wanted = {
        (row["player_id"], row["aircraft_id"]): clean_counters(row)
        for row in counted_sorties()
        .filter(player_id__in=chunk)
        .values("player_id", "aircraft_id")
        .annotate(**SORTIE_COUNTERS)
    }
    _sync(PlayerAircraft, ("player_id", "aircraft_id"), wanted, PlayerAircraft.objects.filter(player_id__in=chunk))


def _recompute_pools(chunk: list[int]) -> None:
    """`PlayerPool`: the all-time counters per propulsion (prop / jet), for the split leaderboards."""
    wanted = {
        (row["player_id"], row["aircraft__propulsion"]): clean_counters(row)
        for row in counted_sorties()
        .filter(player_id__in=chunk, aircraft__propulsion__in=Propulsion.values)
        .values("player_id", "aircraft__propulsion")
        .annotate(**SORTIE_COUNTERS)
    }
    _sync(PlayerPool, ("player_id", "propulsion"), wanted, PlayerPool.objects.filter(player_id__in=chunk))


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
    pools = PlayerTourPool.objects.filter(player_id__in=chunk)
    if tour_ids is not None:
        pools = pools.filter(tour_id__in=tour_ids)
    wanted_pools = {
        (row["player_id"], row["mission__tour_id"], row["aircraft__propulsion"]): clean_counters(row)
        for row in sorties.filter(aircraft__propulsion__in=Propulsion.values)
        .values("player_id", "mission__tour_id", "aircraft__propulsion")
        .annotate(**SORTIE_COUNTERS)
    }
    _sync(PlayerTourPool, ("player_id", "tour_id", "propulsion"), wanted_pools, pools)


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
    update_rows(model, changed, list(COUNTER_FIELDS))
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
    update_rows(Player, changed_players, ["current_name", "name_lower", "first_seen", "last_seen"])
    update_rows(PlayerName, changed_names, ["first_seen", "last_seen"])
    PlayerName.objects.bulk_create(new_names)
