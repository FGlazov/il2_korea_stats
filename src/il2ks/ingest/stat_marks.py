"""Level-2 stat thresholds (FR-WEB-22, doc 14): the percentiles `core.stat_marks` defines, over the players' counters.

`recompute_thresholds` runs after the player rows are recomputed, once per saved mission (not per player) and once per
rebuild, so incremental == rebuild by construction. It reads one value tuple per qualifying player (all-time: `Player`
rows with at least `min_sorties` sorties; per tour: the `PlayerTour` rows), a few thousand rows at most, and rewrites
only the scopes it is asked for: all-time always, per tour for the tours the mission touched (all tours on a rebuild).
Hidden players count (hiding is presentation only, FR-ADM-3). A scope with too few pilots keeps no rows.
"""

from collections.abc import Iterable

from django.db.models import QuerySet

from il2ks.core.stat_marks import DEFAULT_MARK_RULES, METRICS, MarkRules, Thresholds, Totals, metric_value, thresholds
from il2ks.db.models import Player, PlayerTour, StatThreshold, Tour

_FIELDS = (
    "sorties",
    "deaths",
    "planes_lost",
    "kills_air",
    "kills_ground",
    "flight_time_s",
    "taxi_accidents",
    "friendly_fire_incidents",
)


def recompute_thresholds(rules: MarkRules = DEFAULT_MARK_RULES, tour_ids: Iterable[int] | None = None) -> None:
    """Rewrite the all-time thresholds and those of `tour_ids` (None = every tour) from the level-2 counters."""
    _write(None, rules, Player.objects.all())
    ids = Tour.objects.values_list("pk", flat=True) if tour_ids is None else sorted(set(tour_ids))
    for tour_id in ids:
        _write(tour_id, rules, PlayerTour.objects.filter(tour_id=tour_id))


def _write(tour_id: int | None, rules: MarkRules, rows: QuerySet[Player] | QuerySet[PlayerTour]) -> None:
    population = [
        Totals(*values)
        for values in rows.filter(sorties__gte=rules.min_sorties).values_list(*_FIELDS)  # one row per qualifying pilot
    ]
    wanted: dict[str, Thresholds] = {}
    for metric in METRICS:
        found = thresholds(metric_value(metric, totals) for totals in population)
        if found is not None:
            wanted[metric] = found
    existing = {row.metric: row for row in StatThreshold.objects.filter(tour_id=tour_id)}
    changed: list[StatThreshold] = []
    new: list[StatThreshold] = []
    for metric, found in wanted.items():
        row = existing.pop(metric, None)
        values = {
            "min_sorties": rules.min_sorties,
            "population": found.population,
            "p10": found.p10,
            "p25": found.p25,
            "p50": found.p50,
            "p75": found.p75,
            "p90": found.p90,
        }
        if row is None:
            new.append(StatThreshold(tour_id=tour_id, metric=metric, **values))
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    StatThreshold.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()  # too few pilots now
    StatThreshold.objects.bulk_update(changed, ["min_sorties", "population", "p10", "p25", "p50", "p75", "p90"])
    StatThreshold.objects.bulk_create(new)
