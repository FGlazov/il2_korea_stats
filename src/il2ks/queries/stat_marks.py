"""Read behind the stat marks (FR-WEB-22): one simple SELECT of the stored thresholds. No aggregation (TD-22)."""

from il2ks.db.models import StatThreshold, Tour


def stat_thresholds(tour: Tour | None) -> dict[str, StatThreshold]:
    """The thresholds of one scope by metric key (`core.stat_marks.Metric`): all-time for None, else that tour's.
    Empty when the scope has too few qualifying pilots (then nothing is marked). One query."""
    rows = StatThreshold.objects.filter(tour_id=None if tour is None else tour.pk)
    return {row.metric: row for row in rows}
