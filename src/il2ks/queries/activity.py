"""Reads behind the activity chart (FR-WEB-16): `ActivityDay` rows written at ingest, no aggregation (TD-22)."""

from datetime import timedelta

from il2ks.db.models import ActivityDay, Tour

ACTIVITY_DAYS = 30
TOUR_ACTIVITY_DAYS = 120  # a tour is a month or N days; this only caps an unusually long manual tour


def recent_activity(days: int = ACTIVITY_DAYS, tour: Tour | None = None) -> list[ActivityDay]:
    """The latest `days` days that have activity, oldest first. Days without a mission have no row; the page fills
    the gaps. The window ends at the newest day with a row, not at "today", so the page stays the same until the data
    changes (the ETag only follows the data version, TD-28).

    With a `tour` (OQ-79) the window is the tour's own UTC days, at most `TOUR_ACTIVITY_DAYS` of them (the newest).
    The rows are per UTC day, so a tour whose boundary is not midnight UTC shares its edge days with its neighbours."""
    rows = ActivityDay.objects.all()
    if tour is not None:
        rows = rows.filter(day__gte=tour.started_at.date())
        if tour.ended_at is not None:
            rows = rows.filter(day__lte=(tour.ended_at - timedelta(microseconds=1)).date())
        days = TOUR_ACTIVITY_DAYS
    return list(reversed(rows.order_by("-day")[:days]))
