"""Level-2 stat thresholds (FR-WEB-22, doc 14): the percentiles `core.stat_marks` defines, over the players' counters.

`recompute_thresholds` runs after the player rows are recomputed, once per saved mission (not per player) and once per
rebuild, so incremental == rebuild by construction. It reads one value tuple per qualifying player (all-time: `Player`
rows with at least `min_sorties` sorties; per tour: the `PlayerTour` rows; the Elo marks of a tour: that tour's
`PlayerTourPool` rows, OQ-128), a few thousand rows at most, and rewrites
only the scopes it is asked for: all-time always, per tour for the tours the mission touched (all tours on a rebuild).
Hidden players count (hiding is presentation only, FR-ADM-3). A scope with too few pilots keeps no rows.
"""

from collections.abc import Iterable

from django.db.models import Count, Q, QuerySet

from il2ks.core.stat_marks import (
    DEFAULT_MARK_RULES,
    ELO_METRICS,
    METRICS,
    SORTIE_METRICS,
    Histogram,
    MarkRules,
    Metric,
    SortieMetric,
    Thresholds,
    Totals,
    histogram_thresholds,
    merge_histograms,
    metric_value,
    thresholds,
)
from il2ks.db.models import Player, PlayerTour, PlayerTourPool, SortieThreshold, StatThreshold, Tour
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import delete_pks, update_rows

_FIELDS = (
    "sorties",
    "deaths",
    "planes_lost",
    "kills_air",
    "kills_ground",
    "flight_time_s",
    "taxi_accidents",
    "friendly_kills",
    "score_air",
    "score_ground",
    "score_ground_attack",
    "time_on_target_s",
    "flight_time_air_s",
    "kills_intercept",
    "kills_tank_attack",
    "attack_sorties",
)
_AMOUNT_FIELD: dict[Metric, str] = {  # the column `core.stat_marks.amount` compares with a metric's minimum
    "elo_prop": "elo_prop_games",
    "elo_jet": "elo_jet_games",
    "ground_score_hour": "time_on_target_s",
    "tank_hour": "time_on_target_s",
    "interception_hour": "flight_time_air_s",
}
_ELO_FIELDS = ("elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")  # on Player (all time); per tour see below


def recompute_thresholds(rules: MarkRules = DEFAULT_MARK_RULES, tour_ids: Iterable[int] | None = None) -> None:
    """Rewrite the all-time thresholds and those of `tour_ids` (None = every tour) from the level-2 counters."""
    _write(None, rules, Player.objects.all(), _FIELDS + _ELO_FIELDS)
    ids = Tour.objects.values_list("pk", flat=True) if tour_ids is None else sorted(set(tour_ids))
    for tour_id in ids:
        _write(tour_id, rules, PlayerTour.objects.filter(tour_id=tour_id), _FIELDS)
        _write_sortie_tour(tour_id)
    _write_sortie_all_time()


_SORTIE_COLUMN: dict[SortieMetric, str] = {"air_kills": "kills_air", "ground_kills": "kills_ground"}


def _save_sortie_rows(tour_id: int | None, histograms: dict[SortieMetric, Histogram]) -> None:
    """Make the `SortieThreshold` rows of one scope equal these histograms. Like `StatThreshold`, no row means too few
    sorties for a distribution (`MIN_POPULATION`); a tour that small adds nothing to all time either (negligible)."""
    existing = {row.metric: row for row in SortieThreshold.objects.filter(tour_id=tour_id)}
    for metric, histogram in histograms.items():
        found = histogram_thresholds(histogram)
        if found is None:
            continue
        population = found.population
        values = {
            "population": population,
            "histogram": {str(value): count for value, count in sorted(histogram.items())},
            "p10": found.p10,
            "p25": found.p25,
            "p50": found.p50,
            "p75": found.p75,
            "p90": found.p90,
            "p95": found.p95,
            "p99": found.p99,
        }
        row = existing.pop(metric, None)
        if row is None:
            SortieThreshold.objects.create(tour_id=tour_id, metric=metric, **values)
        elif any(getattr(row, name) != value for name, value in values.items()):
            SortieThreshold.objects.filter(pk=row.pk).update(**values)
    delete_pks(SortieThreshold.objects, [row.pk for row in existing.values()])


def _write_sortie_tour(tour_id: int) -> None:
    """The sortie populations of one tour (the sortie page's marks): one GROUP BY per metric over the tour's counted
    pilot sorties, which are kept as histograms (value -> sorties) so that all time can be summed from them."""
    histograms: dict[SortieMetric, Histogram] = {}
    sorties = counted_sorties().filter(tour_id=tour_id)
    for metric in SORTIE_METRICS:
        column = _SORTIE_COLUMN[metric]
        rows = sorties.order_by().values_list(column).annotate(n=Count("pk"))
        histograms[metric] = {int(value): int(count) for value, count in rows}
    _save_sortie_rows(tour_id, histograms)


def _write_sortie_all_time() -> None:
    """The all-time sortie populations: the sum of the tours' stored histograms, exact, no sortie is read."""
    stored = SortieThreshold.objects.filter(tour__isnull=False).order_by("tour_id").values_list("metric", "histogram")
    parts: dict[str, list[Histogram]] = {}
    for metric, histogram in stored:
        parts.setdefault(metric, []).append({int(value): count for value, count in histogram.items()})
    _save_sortie_rows(None, {metric: merge_histograms(parts.get(metric, [])) for metric in SORTIE_METRICS})


def _tour_elo_totals(tour_id: int) -> list[Totals]:
    """One `Totals` per rated pool row of the tour, with only that pool's Elo set (the other metrics read 0 sorties, so
    these rows are never part of their populations; `amount` of an Elo metric is the pool's games)."""
    found: list[Totals] = []
    rows = PlayerTourPool.objects.filter(tour_id=tour_id, elo_games__gt=0).order_by("pk")
    for propulsion, elo, games in rows.values_list("propulsion", "elo", "elo_games"):
        if propulsion == "prop":
            found.append(Totals(0, 0, 0, 0, 0, 0.0, elo_prop=elo, elo_prop_games=games))
        else:
            found.append(Totals(0, 0, 0, 0, 0, 0.0, elo_jet=elo, elo_jet_games=games))
    return found


def _write(
    tour_id: int | None, rules: MarkRules, rows: QuerySet[Player] | QuerySet[PlayerTour], fields: tuple[str, ...]
) -> None:
    metrics: list[Metric] = list(METRICS)
    # Only rows that reach at least one metric's minimum can count: load those (the OR of the per-metric minimums).
    eligible = Q(pk__in=[])
    for metric in metrics:
        if tour_id is not None and metric in ELO_METRICS:
            continue  # a tour's Elo marks come from its pool rows, not from `rows`
        eligible |= Q(**{f"{_AMOUNT_FIELD.get(metric, 'sorties')}__gte": rules.minimum(metric)})
    pilots = [Totals(**dict(zip(fields, values, strict=True))) for values in rows.filter(eligible).values_list(*fields)]
    elo_pilots = _tour_elo_totals(tour_id) if tour_id is not None else []  # a tour's Elo marks: its pool rows
    wanted: dict[Metric, Thresholds] = {}
    for metric in metrics:
        # each metric has its own population: sorties, encounters or time on target (rules.qualifies)
        population = elo_pilots if tour_id is not None and metric in ELO_METRICS else pilots
        found = thresholds(metric_value(metric, totals) for totals in population if rules.qualifies(metric, totals))
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
            "p95": found.p95,
            "p99": found.p99,
        }
        if row is None:
            new.append(StatThreshold(tour_id=tour_id, metric=metric, **values))
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    delete_pks(StatThreshold.objects, [row.pk for row in existing.values()])  # too few pilots now
    update_rows(StatThreshold, changed, ["min_sorties", "population", "p10", "p25", "p50", "p75", "p90", "p95", "p99"])
    StatThreshold.objects.bulk_create(new)
