"""Read behind the stat marks (FR-WEB-22): one simple SELECT of the stored thresholds. No aggregation (TD-22)."""

from django.db.models import Q

from il2ks.core.stat_marks import ELO_METRICS, MIN_POPULATION
from il2ks.db.models import SortieThreshold, StatThreshold, Tour


def stat_thresholds(tour: Tour | None) -> dict[str, StatThreshold]:
    """The thresholds of one scope by metric key (`core.stat_marks.Metric`): all-time for None, else that tour's.
    Empty when the scope has too few qualifying pilots (then nothing is marked). One query. A tour scope also gets the
    all-time Elo thresholds (ratings are not per tour, so the Elo marks compare with the all-time population)."""
    if tour is None:
        rows = StatThreshold.objects.filter(tour_id=None)
    else:
        rows = StatThreshold.objects.filter(Q(tour_id=tour.pk) | Q(tour_id=None, metric__in=ELO_METRICS))
    return {row.metric: row for row in rows}


def sortie_thresholds(tour_id: int | None) -> dict[tuple[str, bool], SortieThreshold]:
    """The sortie populations the sortie page compares with (FR-WEB-22, 2026-10-05): the percentiles of the counted
    pilot sorties of the sortie's tour and of all time, by (metric key, all_time). Only populations with enough sorties
    (`MIN_POPULATION`). One query; the histograms are deferred."""
    scopes = Q(tour_id=None) if tour_id is None else Q(tour_id=tour_id) | Q(tour_id=None)
    rows = SortieThreshold.objects.filter(scopes, population__gte=MIN_POPULATION).defer("histogram")
    return {(row.metric, row.tour_id is None): row for row in rows}
