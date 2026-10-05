"""Ironman streaks (`PlayerStreak`, `PlayerBestStreak`, `PlayerStreakRun`, FR-WEB-23): level 2, per player and track.

Every pilot has an all, an air and a ground ironman run (maintainer, 2026-10-05, `il2ks.core.streaks.Track`): the rule
runs on the sorties of one track at a time (an attack sortie is a ground-track sortie, every other one an air-track
sortie, and both are in the all track), so a death in an attack sortie never ends the air run and the reverse, while
any death ends the all run. All tables carry a `track`.

A new tour is a clean slate (maintainer, 2026-10-05): a streak never crosses a tour boundary, so the rule
(`il2ks.core.streaks`) runs over one tour's counted (pilot) sorties at a time, in chronological order (spawn time, then
id). Two steps, both per chunk of players:

1. `refresh_streak_tours(chunk, tour_ids)`: rewrite the per-tour rows of these tours (None = every tour of the players):
   `PlayerBestStreak` (best by sorties, by air kills, by flight time) and `PlayerStreakRun` (every run of at least
   `MIN_LISTED_RUN` survived sorties, OQ-82, with the sortie that ended it).
2. `rollup_streaks(chunk)`: the all-time rows come from the tour rows alone (no read of the whole history) `[PROPOSED]`:
   the all-time best (`tour` null) is the best over the tours' bests (a tie: the earlier one wins), the all-time run
   list is the union of the tours' runs (never merged), and `PlayerStreak.best_*` is the all-time best by sorties.
   `PlayerStreak.current_*` is the run in the *current* tour, the newest one (PRODUCT, `[PROPOSED]`): a new tour starts
   everybody at zero, and a player who has not flown in it yet shows no current streak. `current_tour` says which tour
   the current run belongs to; rows pointing at an older tour are zeroed when a newer tour exists.

A player with no survived sortie has no `PlayerStreak` row. Sorties of a mission without
a tour (only before the first `rebuild-aggregates` assigned tours) count nowhere.
"""

from collections.abc import Iterable
from datetime import datetime

from django.db.models import Q, QuerySet

from il2ks.core.streaks import RunEnd, Streak, StreakRun, StreakSortie, StreakSummary, Track, runs, summarize, track_of
from il2ks.db.models import (
    Outcome,
    Player,
    PlayerBestStreak,
    PlayerSortie,
    PlayerStreak,
    PlayerStreakRun,
    StreakKind,
    Tour,
)
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import delete_pks, update_partial_rows, update_rows

_FIELDS = (
    "current_sorties",
    "current_kills_air",
    "current_kills_ground",
    "current_flight_time_s",
    "current_since",
    "current_until",
    "best_sorties",
    "best_kills_air",
    "best_kills_ground",
    "best_flight_time_s",
    "best_since",
    "best_until",
    "current_tour_id",
)

type _BestKey = tuple[int, int | None, str, str]  # player, tour (None = all time), track, kind
type _BestValues = tuple[
    int, int, int, float, datetime, datetime
]  # sorties, air kills, ground kills, flight, since, until


def _read(chunk: list[int], tours: set[int] | None) -> dict[tuple[int, int], list[StreakSortie]]:
    """The counted sorties of the players per (player, tour), chronological; `tours` None = every tour."""
    sorties: QuerySet[PlayerSortie] = (
        counted_sorties().filter(player_id__in=chunk, tour_id__isnull=False).order_by("player_id", "spawned_at", "pk")
    )
    if tours is not None:
        sorties = sorties.filter(tour_id__in=tours)
    found: dict[tuple[int, int], list[StreakSortie]] = {}
    for (
        sortie_id,
        pid,
        tour_id,
        spawned,
        ended,
        kills,
        flight,
        death,
        captured,
        outcome,
        kills_ground,
        role,
    ) in sorties.values_list(
        "pk",
        "player_id",
        "tour_id",
        "spawned_at",
        "ended_at",
        "kills_air",
        "flight_time_s",
        "is_death",
        "is_captured",
        "outcome",
        "kills_ground",
        "combat_role",
    ).iterator():
        row = StreakSortie(
            spawned,
            ended,
            kills,
            flight,
            death,
            captured,
            outcome == Outcome.NOT_TAKEN_OFF,
            sortie_id,
            kills_ground,
            track_of(role),
        )
        found.setdefault((pid, tour_id), []).append(row)
    return found


def refresh_streak_tours(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the per-tour best-streak and run rows of these players in `tour_ids` (None = all tours) equal what their
    sorties of the tour say."""
    tours = None if tour_ids is None else set(tour_ids)
    wanted_best: dict[_BestKey, _BestValues] = {}
    wanted_runs: dict[_RunScope, list[StreakRun]] = {}
    for (pid, tour_id), rows in _read(chunk, tours).items():
        for track in Track:
            summary = summarize(rows, track)
            if summary.best.sorties:
                wanted_best.update(_best_rows(pid, tour_id, track, summary))
                wanted_runs[(pid, tour_id, track.value)] = runs(rows, track)
    _sync_best(chunk, tours, wanted_best, all_time=False)
    _sync_runs(chunk, tours, wanted_runs, all_time=False)


def rollup_streaks(chunk: list[int]) -> None:
    """Make the all-time rows of these players follow their per-tour rows (see the module docstring). Reads the tour
    rows, and the sorties of the current tour only (for the current run)."""
    best_rows = PlayerBestStreak.objects.filter(player_id__in=chunk, tour__isnull=False).order_by("since", "pk")
    wanted_best: dict[_BestKey, _BestValues] = {}
    for row in best_rows:  # chronological, strict improvement: the earlier of equals stays
        key = (row.player_id, None, row.track, row.kind)
        values = (row.sorties, row.kills_air, row.kills_ground, row.flight_time_s, row.since, row.until)
        have = wanted_best.get(key)
        if have is None or _rank(row.track, row.kind, values) > _rank(row.track, row.kind, have):
            wanted_best[key] = values
    wanted_runs: dict[_RunScope, list[StreakRun]] = {}
    for run in PlayerStreakRun.objects.filter(player_id__in=chunk, tour__isnull=False).order_by("since", "pk"):
        streak = Streak(run.sorties, run.kills_air, run.flight_time_s, run.since, run.until, run.kills_ground)
        wanted_runs.setdefault((run.player_id, None, run.track), []).append(
            StreakRun(streak, RunEnd(run.ended_by), run.ended_sortie_id)
        )

    newest = Tour.objects.order_by("-started_at").values_list("pk", flat=True).first()
    current: dict[tuple[int, str], StreakSummary] = {}
    if newest is not None:
        for (pid, _), rows in _read(chunk, {newest}).items():
            for track in Track:
                current[(pid, track.value)] = summarize(rows, track)
    wanted: dict[tuple[int, str], tuple[object, ...]] = {}
    for (pid, _, track, kind), values in wanted_best.items():
        if kind == StreakKind.SORTIES.value:
            here = current.get((pid, track))
            wanted[(pid, track)] = (*_values(here.current if here else Streak()), *values, newest if here else None)

    _sync_streaks(chunk, wanted)
    _sync_best(chunk, None, wanted_best, all_time=True)
    _sync_runs(chunk, None, wanted_runs, all_time=True)
    _sync_player_columns(chunk, wanted_best)
    if newest is not None:  # a newer tour exists: nobody's current run is still going in an older one
        for track in Track:  # per track: the (track, -current_sorties) index serves each update
            PlayerStreak.objects.filter(
                Q(current_sorties__gt=0) | Q(current_tour__isnull=False), track=track.value
            ).exclude(current_tour_id=newest).update(
                current_sorties=0,
                current_kills_air=0,
                current_kills_ground=0,
                current_flight_time_s=0.0,
                current_since=None,
                current_until=None,
                current_tour=None,
            )


def _rank(track: str, kind: str, v: _BestValues) -> tuple[float, float, float]:
    """The comparison key of a best streak of `kind` on `track` (as `core.streaks`: the criterion first, then the other
    two, the track's kills being the ones that count)."""
    sorties, air, ground, flight = float(v[0]), float(v[1]), float(v[2]), v[3]
    own = {Track.GROUND.value: ground, Track.ALL.value: air + ground}.get(track, air)  # what the track's kills are
    if kind in (StreakKind.AIR_KILLS.value, StreakKind.GROUND_KILLS.value, StreakKind.KILLS.value):
        return (own, sorties, flight)
    if kind == StreakKind.FLIGHT_TIME.value:
        return (flight, sorties, own)
    return (sorties, own, flight)


def _sync_streaks(chunk: list[int], wanted: dict[tuple[int, str], tuple[object, ...]]) -> None:
    existing = {(r.player_id, r.track): r for r in PlayerStreak.objects.filter(player_id__in=chunk)}
    changed: list[PlayerStreak] = []
    new: list[PlayerStreak] = []
    for (pid, track), values in wanted.items():
        row = existing.pop((pid, track), None)
        if row is None:
            new.append(PlayerStreak(player_id=pid, track=track, **dict(zip(_FIELDS, values, strict=True))))
        elif tuple(getattr(row, f) for f in _FIELDS) != values:
            for field, value in zip(_FIELDS, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    delete_pks(PlayerStreak.objects, [r.pk for r in existing.values()])
    update_rows(PlayerStreak, changed, list(_FIELDS))
    PlayerStreak.objects.bulk_create(new)


def _sync_best(
    chunk: list[int], tours: set[int] | None, wanted: dict[_BestKey, _BestValues], *, all_time: bool
) -> None:
    """Make the chunk's best-streak rows of one scope equal `wanted`: the all-time rows (`all_time`), else the rows of
    `tours` (None = every tour)."""
    rows = PlayerBestStreak.objects.filter(player_id__in=chunk)
    if all_time:
        rows = rows.filter(tour__isnull=True)
    else:
        rows = rows.filter(tour__isnull=False)
        if tours is not None:
            rows = rows.filter(tour_id__in=tours)
    existing = {(r.player_id, r.tour_id, r.track, r.kind): r for r in rows}
    changed: list[PlayerBestStreak] = []
    new: list[PlayerBestStreak] = []
    fields = ("sorties", "kills_air", "kills_ground", "flight_time_s", "since", "until")
    for (pid, tour_id, track, kind), values in wanted.items():
        row = existing.pop((pid, tour_id, track, kind), None)
        if row is None:
            new.append(
                PlayerBestStreak(
                    player_id=pid, tour_id=tour_id, track=track, kind=kind, **dict(zip(fields, values, strict=True))
                )
            )
        elif tuple(getattr(row, f) for f in fields) != values:
            for field, value in zip(fields, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    delete_pks(PlayerBestStreak.objects, [r.pk for r in existing.values()])
    update_rows(PlayerBestStreak, changed, list(fields))
    PlayerBestStreak.objects.bulk_create(new)


type _RunScope = tuple[int, int | None, str]  # player, tour (None = all time), track
type _RunKey = tuple[int, int | None, str, datetime | None]  # scope + first spawn
type _RunValues = tuple[
    int, int, int, float, datetime | None, str, int | None
]  # sorties, kills_air, kills_ground, flight, until, ended_by, sortie


def _sync_runs(
    chunk: list[int],
    tours: set[int] | None,
    wanted_runs: dict[_RunScope, list[StreakRun]],
    *,
    all_time: bool,
) -> None:
    """Make the `PlayerStreakRun` rows of the chunk in one scope (all-time, or `tours`: None = every tour) equal the
    runs found."""
    fields = ("sorties", "kills_air", "kills_ground", "flight_time_s", "until", "ended_by", "ended_sortie_id")
    wanted: dict[_RunKey, _RunValues] = {}
    for (pid, tour_id, track), found in wanted_runs.items():
        for run in found:
            streak = run.streak
            wanted[(pid, tour_id, track, streak.since)] = (
                streak.sorties,
                streak.kills_air,
                streak.kills_ground,
                streak.flight_time_s,
                streak.until,
                run.end.value,
                run.ended_by_ref,
            )
    rows = PlayerStreakRun.objects.filter(player_id__in=chunk)
    if all_time:
        rows = rows.filter(tour__isnull=True)
    else:
        rows = rows.filter(tour__isnull=False)
        if tours is not None:
            rows = rows.filter(tour_id__in=tours)
    existing: dict[_RunKey, PlayerStreakRun] = {(r.player_id, r.tour_id, r.track, r.since): r for r in rows}
    changed: list[PlayerStreakRun] = []
    new: list[PlayerStreakRun] = []
    for key, values in wanted.items():
        pid, tour_id, track, since = key
        row: PlayerStreakRun | None = existing.pop(key, None)
        if row is None:
            new.append(
                PlayerStreakRun(
                    player_id=pid, tour_id=tour_id, track=track, since=since, **dict(zip(fields, values, strict=True))
                )
            )
        elif tuple(getattr(row, f) for f in fields) != values:
            for field, value in zip(fields, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    delete_pks(PlayerStreakRun.objects, [r.pk for r in existing.values()])
    update_rows(
        PlayerStreakRun,
        changed,
        ["sorties", "kills_air", "kills_ground", "flight_time_s", "until", "ended_by", "ended_sortie"],
    )
    PlayerStreakRun.objects.bulk_create(new)


def _sync_player_columns(chunk: list[int], wanted_best: dict[_BestKey, _BestValues]) -> None:
    """Copy the player list's two streak columns (`Player.streak_kills_air` / `streak_kills_ground`) from the all-time
    best rows: the kills of the best air run by air kills, and of the best ground run by ground kills."""
    changed: list[Player] = []
    for player in Player.objects.filter(pk__in=chunk).only("pk", "streak_kills_air", "streak_kills_ground"):
        air = wanted_best.get((player.pk, None, Track.AIR.value, StreakKind.AIR_KILLS.value))
        ground = wanted_best.get((player.pk, None, Track.GROUND.value, StreakKind.GROUND_KILLS.value))
        air_kills, ground_kills = (air[1] if air else 0), (ground[2] if ground else 0)
        if (player.streak_kills_air, player.streak_kills_ground) != (air_kills, ground_kills):
            player.streak_kills_air, player.streak_kills_ground = air_kills, ground_kills
            changed.append(player)
    update_partial_rows(Player, changed, ["streak_kills_air", "streak_kills_ground"])


def _best_rows(
    player_id: int, tour_id: int | None, track: Track, summary: StreakSummary
) -> dict[_BestKey, _BestValues]:
    """The best-streak rows of one scope and track. A kills row needs at least one kill (of the track's kind) in the
    streak."""
    rows = {
        (player_id, tour_id, track.value, StreakKind.SORTIES.value): _best_values(summary.best),
        (player_id, tour_id, track.value, StreakKind.FLIGHT_TIME.value): _best_values(summary.best_flight_time),
    }
    if summary.best_kills.kills_of(track):
        kind = {Track.GROUND: StreakKind.GROUND_KILLS, Track.ALL: StreakKind.KILLS}.get(track, StreakKind.AIR_KILLS)
        rows[(player_id, tour_id, track.value, kind.value)] = _best_values(summary.best_kills)
    return rows


def _best_values(s: Streak) -> _BestValues:
    assert s.since is not None
    assert s.until is not None
    return (s.sorties, s.kills_air, s.kills_ground, s.flight_time_s, s.since, s.until)


def _values(s: Streak) -> tuple[object, ...]:
    return (s.sorties, s.kills_air, s.kills_ground, s.flight_time_s, s.since, s.until)
