"""Ironman streaks (`PlayerStreak`, FR-WEB-23): level 2, recomputed per player from their counted (pilot) sorties.

The rule is `il2ks.core.streaks`; this module reads the sorties in chronological order (spawn time, then id) and writes
the rows that changed. A player with no survived sortie has no row."""

from django.db.models import QuerySet

from il2ks.core.streaks import Streak, StreakSortie, summarize
from il2ks.db.models import Outcome, PlayerSortie, PlayerStreak
from il2ks.ingest.counters import counted_sorties

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


def recompute_streaks(chunk: list[int]) -> None:
    sorties: QuerySet[PlayerSortie] = (
        counted_sorties().filter(player_id__in=chunk).order_by("player_id", "spawned_at", "pk")
    )
    by_player: dict[int, list[StreakSortie]] = {}
    for pid, spawned, ended, kills, flight, death, captured, outcome in sorties.values_list(
        "player_id", "spawned_at", "ended_at", "kills_air", "flight_time_s", "is_death", "is_captured", "outcome"
    ).iterator():
        by_player.setdefault(pid, []).append(
            StreakSortie(spawned, ended, kills, flight, death, captured, outcome == Outcome.NOT_TAKEN_OFF)
        )

    wanted: dict[int, tuple[object, ...]] = {}
    for pid, rows in by_player.items():
        summary = summarize(rows)
        if summary.best.sorties:
            wanted[pid] = (*_values(summary.current), *_values(summary.best))

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
    PlayerStreak.objects.bulk_update(changed, list(_FIELDS))
    PlayerStreak.objects.bulk_create(new)


def _values(s: Streak) -> tuple[object, ...]:
    return (s.sorties, s.kills_air, s.flight_time_s, s.since, s.until)
