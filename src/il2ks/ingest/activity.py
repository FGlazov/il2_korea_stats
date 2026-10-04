"""Level-2 server activity per UTC day (`ActivityDay`, FR-WEB-16): the home page's activity chart.

Like the other level-2 tables a day is always recomputed from level 1 (its visible missions and their pilot sorties),
never adjusted by a delta: `recompute_days` for the days a mission save or a hide/unhide touched, `rebuild_activity` for
all of them (`il2ks rebuild-aggregates`). One code path, so incremental == rebuild."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from il2ks.db.models import ActivityDay, Mission, PlayerSortie
from il2ks.ingest.counters import COUNTED_ROLES


def day_of(moment: datetime) -> date:
    """The UTC calendar day of an instant."""
    return moment.astimezone(UTC).date()


def recompute_days(days: Iterable[date]) -> None:
    """Recompute `ActivityDay` for these UTC days; a day without a visible mission loses its row."""
    for day in sorted(set(days)):
        start = datetime.combine(day, time.min, tzinfo=UTC)
        missions = Mission.objects.visible().filter(started_at__gte=start, started_at__lt=start + timedelta(days=1))
        totals = missions.aggregate(n=Count("pk"), sorties=Sum("sorties_total"), kills_air=Sum("kills_air"))
        if not totals["n"]:
            ActivityDay.objects.filter(day=day).delete()
            continue
        pilots = (
            PlayerSortie.objects.filter(mission__in=missions, role__in=COUNTED_ROLES)
            .values("player_id")
            .distinct()
            .count()
        )
        ActivityDay.objects.update_or_create(
            day=day,
            defaults={
                "missions": totals["n"],
                "sorties": totals["sorties"] or 0,
                "kills_air": totals["kills_air"] or 0,
                "pilots": pilots,
            },
        )


def rebuild_activity() -> None:
    """Recompute every day that has a mission, and drop rows of days that no longer have a visible one."""
    mission_days = set(
        Mission.objects.annotate(day=TruncDate("started_at", tzinfo=UTC)).values_list("day", flat=True).distinct()
    )
    stale = set(ActivityDay.objects.values_list("day", flat=True))
    recompute_days(mission_days | stale)
