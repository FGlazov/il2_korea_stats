"""Level-2 stat thresholds (FR-WEB-22, doc 14): the percentiles `core.stat_marks` defines, over the players' counters.

`recompute_thresholds` runs after the player rows are recomputed, once per saved mission (not per player) and once per
rebuild, so incremental == rebuild by construction. It reads one value tuple per qualifying player (all-time: `Player`
rows with at least `min_sorties` sorties; per tour: the `PlayerTour` rows), a few thousand rows at most, and rewrites
only the scopes it is asked for: all-time always, per tour for the tours the mission touched (all tours on a rebuild).
Hidden players count (hiding is presentation only, FR-ADM-3). A scope with too few pilots keeps no rows.
"""

from collections.abc import Iterable

from django.db.models import Q, QuerySet

from il2ks.core.stat_marks import (
    DEFAULT_MARK_RULES,
    ELO_METRICS,
    METRICS,
    MarkRules,
    Metric,
    Thresholds,
    Totals,
    amount,
    metric_value,
    thresholds,
)
from il2ks.db.models import Player, PlayerTour, StatThreshold, Tour
from il2ks.ingest.dbutil import update_rows

_FIELDS = (
    "sorties",
    "deaths",
    "planes_lost",
    "kills_air",
    "kills_ground",
    "flight_time_s",
    "taxi_accidents",
    "friendly_fire_incidents",
    "score_air",
    "score_ground",
    "score_ground_attack",
    "time_on_target_s",
    "flight_time_air_s",
    "kills_intercept",
    "kills_tank_attack",
)
_AMOUNT_FIELD: dict[Metric, str] = {  # the column `core.stat_marks.amount` compares with a metric's minimum
    "elo_prop": "elo_prop_games",
    "elo_jet": "elo_jet_games",
    "ground_score_hour": "time_on_target_s",
    "tank_hour": "time_on_target_s",
    "interception_hour": "flight_time_air_s",
}
_ELO_FIELDS = ("elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")  # Player only: ratings are all time


def recompute_thresholds(rules: MarkRules = DEFAULT_MARK_RULES, tour_ids: Iterable[int] | None = None) -> None:
    """Rewrite the all-time thresholds and those of `tour_ids` (None = every tour) from the level-2 counters."""
    _write(None, rules, Player.objects.all(), _FIELDS + _ELO_FIELDS)
    ids = Tour.objects.values_list("pk", flat=True) if tour_ids is None else sorted(set(tour_ids))
    for tour_id in ids:
        _write(tour_id, rules, PlayerTour.objects.filter(tour_id=tour_id), _FIELDS)


def _write(
    tour_id: int | None, rules: MarkRules, rows: QuerySet[Player] | QuerySet[PlayerTour], fields: tuple[str, ...]
) -> None:
    # The Elo marks compare with the all-time population on a tour profile too
    metrics: list[Metric] = [m for m in METRICS if tour_id is None or m not in ELO_METRICS]
    # Only rows that reach at least one metric's minimum can count: load those (the OR of the per-metric minimums).
    eligible = Q(pk__in=[])
    for metric in metrics:
        eligible |= Q(**{f"{_AMOUNT_FIELD.get(metric, 'sorties')}__gte": rules.minimum(metric)})
    pilots = [Totals(**dict(zip(fields, values, strict=True))) for values in rows.filter(eligible).values_list(*fields)]
    wanted: dict[Metric, Thresholds] = {}
    for metric in metrics:
        minimum = rules.minimum(metric)  # each metric has its own population: sorties, encounters or time on target
        found = thresholds(metric_value(metric, totals) for totals in pilots if amount(metric, totals) >= minimum)
        if found is not None:
            wanted[metric] = found
    existing = {row.metric: row for row in StatThreshold.objects.filter(tour_id=tour_id)}
    changed: list[StatThreshold] = []
    new: list[StatThreshold] = []
    for metric, found in wanted.items():
        row = existing.pop(metric, None)
        values = {
            "min_sorties": rules.minimum(metric),  # the minimum in the metric's unit (core.stat_marks.unit)
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
    update_rows(StatThreshold, changed, ["min_sorties", "population", "p10", "p25", "p50", "p75", "p90"])
    StatThreshold.objects.bulk_create(new)
