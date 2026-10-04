"""Reads behind the activity chart (FR-WEB-16): `ActivityDay` rows written at ingest, no aggregation (TD-22)."""

from il2ks.db.models import ActivityDay

ACTIVITY_DAYS = 30


def recent_activity(days: int = ACTIVITY_DAYS) -> list[ActivityDay]:
    """The latest `days` days that have activity, oldest first. Days without a mission have no row; the page fills
    the gaps. The window ends at the newest day with a row, not at "today", so the page stays the same until the data
    changes (the ETag only follows the data version, TD-28)."""
    return list(reversed(ActivityDay.objects.order_by("-day")[:days]))
