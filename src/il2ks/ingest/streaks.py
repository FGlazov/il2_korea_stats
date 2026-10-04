"""Ironman streaks (`PlayerStreak`, `PlayerBestStreak`, FR-WEB-23): level 2, recomputed per player from their counted
(pilot) sorties.

The rule is `il2ks.core.streaks`; this module reads the sorties in chronological order (spawn time, then id) and writes
the rows that changed. A player with no survived sortie has no row.

- `PlayerStreak`: the current and the best (by sorties) streak over the player's whole history.
- `PlayerBestStreak`: the best streak by sorties, by air kills and by flight time, over the whole history (`tour` null)
  and within each tour (the tour's sorties only, so a streak never spans two tours there). One read of the sorties
  fills both; `tour_ids` limits which tours' rows are rewritten (None = all), the all-time rows always are.
"""

from collections.abc import Iterable

from django.db.models import Q, QuerySet

from il2ks.core.streaks import Streak, StreakSortie, StreakSummary, summarize
from il2ks.db.models import Outcome, PlayerBestStreak, PlayerSortie, PlayerStreak, StreakKind
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows

_FIELDS = (
    "current_sorties",
    "current_kills_air",
    "current_flight_time_s",
    "current_since",
    "current_until",
    "best_sorties",
    "best_kills_air",
    "best_flight_time_s",
    "best_since",
    "best_until",
)

type _BestKey = tuple[int, int | None, str]  # player, tour (None = all time), kind
type _BestValues = tuple[int, int, float, object, object]  # sorties, kills_air, flight_time_s, since, until


def recompute_streaks(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    sorties: QuerySet[PlayerSortie] = (
        counted_sorties().filter(player_id__in=chunk).order_by("player_id", "spawned_at", "pk")
    )
    tours = None if tour_ids is None else set(tour_ids)
    by_player: dict[int, list[StreakSortie]] = {}
    by_player_tour: dict[tuple[int, int], list[StreakSortie]] = {}
    for pid, tour_id, spawned, ended, kills, flight, death, captured, outcome in sorties.values_list(
        "player_id",
        "mission__tour_id",
        "spawned_at",
        "ended_at",
        "kills_air",
        "flight_time_s",
        "is_death",
        "is_captured",
        "outcome",
    ).iterator():
        row = StreakSortie(spawned, ended, kills, flight, death, captured, outcome == Outcome.NOT_TAKEN_OFF)
        by_player.setdefault(pid, []).append(row)
        if tour_id is not None and (tours is None or tour_id in tours):
            by_player_tour.setdefault((pid, tour_id), []).append(row)

    wanted: dict[int, tuple[object, ...]] = {}
    wanted_best: dict[_BestKey, _BestValues] = {}
    for pid, rows in by_player.items():
        summary = summarize(rows)
        if summary.best.sorties:
            wanted[pid] = (*_values(summary.current), *_values(summary.best))
            wanted_best.update(_best_rows(pid, None, summary))
    for (pid, tour_id), rows in by_player_tour.items():
        summary = summarize(rows)
        if summary.best.sorties:
            wanted_best.update(_best_rows(pid, tour_id, summary))

    _sync_streaks(chunk, wanted)
    _sync_best(chunk, tours, wanted_best)


def _sync_streaks(chunk: list[int], wanted: dict[int, tuple[object, ...]]) -> None:
    existing = {r.player_id: r for r in PlayerStreak.objects.filter(player_id__in=chunk)}
    changed: list[PlayerStreak] = []
    new: list[PlayerStreak] = []
    for pid, values in wanted.items():
        row = existing.pop(pid, None)
        if row is None:
            new.append(PlayerStreak(player_id=pid, **dict(zip(_FIELDS, values, strict=True))))
        elif tuple(getattr(row, f) for f in _FIELDS) != values:
            for field, value in zip(_FIELDS, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    PlayerStreak.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(PlayerStreak, changed, list(_FIELDS))
    PlayerStreak.objects.bulk_create(new)


def _sync_best(chunk: list[int], tours: set[int] | None, wanted: dict[_BestKey, _BestValues]) -> None:
    rows = PlayerBestStreak.objects.filter(player_id__in=chunk)
    if tours is not None:  # all-time rows (tour null) and the touched tours' rows
        rows = rows.filter(Q(tour_id__isnull=True) | Q(tour_id__in=tours))
    existing = {(r.player_id, r.tour_id, r.kind): r for r in rows}
    changed: list[PlayerBestStreak] = []
    new: list[PlayerBestStreak] = []
    fields = ("sorties", "kills_air", "flight_time_s", "since", "until")
    for (pid, tour_id, kind), values in wanted.items():
        row = existing.pop((pid, tour_id, kind), None)
        if row is None:
            new.append(
                PlayerBestStreak(player_id=pid, tour_id=tour_id, kind=kind, **dict(zip(fields, values, strict=True)))
            )
        elif tuple(getattr(row, f) for f in fields) != values:
            for field, value in zip(fields, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    PlayerBestStreak.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    PlayerBestStreak.objects.bulk_update(changed, list(fields))
    PlayerBestStreak.objects.bulk_create(new)


def _best_rows(player_id: int, tour_id: int | None, summary: StreakSummary) -> dict[_BestKey, _BestValues]:
    """The best-streak rows of one scope. An air-kills row needs at least one air kill in the streak."""
    rows = {
        (player_id, tour_id, StreakKind.SORTIES.value): _best_values(summary.best),
        (player_id, tour_id, StreakKind.FLIGHT_TIME.value): _best_values(summary.best_flight_time),
    }
    if summary.best_air_kills.kills_air:
        rows[(player_id, tour_id, StreakKind.AIR_KILLS.value)] = _best_values(summary.best_air_kills)
    return rows


def _best_values(s: Streak) -> _BestValues:
    return (s.sorties, s.kills_air, s.flight_time_s, s.since, s.until)


def _values(s: Streak) -> tuple[object, ...]:
    return (s.sorties, s.kills_air, s.flight_time_s, s.since, s.until)
