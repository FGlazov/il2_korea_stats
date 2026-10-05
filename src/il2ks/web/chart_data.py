"""The chart specs of the pages (FR-WEB-16): stored level-2 rows in, `ChartSpec` out. Presentation only (TD-22): a chart
reads rows that ingest already summed; this module only orders them, fills days without activity with zeros and
words the titles."""

from collections.abc import Sequence
from datetime import timedelta

from django.utils.dateformat import format as format_date
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from il2ks.db.models import ActivityDay, PlayerTour
from il2ks.queries.activity import ACTIVITY_DAYS
from il2ks.queries.tours import tour_title
from il2ks.web.charts import ChartSeries, ChartSpec

MIN_TOURS = 2  # one tour is a single bar the profile tiles already show


def player_charts(rows: Sequence[PlayerTour]) -> tuple[ChartSpec, ...]:
    """The profile's per-tour charts from `queries.players.tour_history` (oldest first): sorties per tour, and air
    kills next to deaths per tour. Nothing for fewer than two tours."""
    if len(rows) < MIN_TOURS:
        return ()
    tours = tuple(tour_title(row.tour.title) for row in rows)
    n = len(rows)
    desc = ngettext(
        "Bar chart over %(n)d tour. The numbers are in the table below.",
        "Bar chart over %(n)d tours. The numbers are in the table below.",
        n,
    ) % {"n": n}
    return (
        ChartSpec(
            "chart-tour-sorties",
            _("Sorties per tour"),
            desc,
            tours,
            (ChartSeries(_("Sorties"), tuple(row.sorties for row in rows)),),
            label_width=84,
        ),
        ChartSpec(
            "chart-tour-kills",
            _("Air kills and deaths per tour"),
            desc,
            tours,
            (
                ChartSeries(_("Air kills"), tuple(row.kills_air for row in rows)),
                ChartSeries(_("Deaths"), tuple(row.deaths for row in rows)),
            ),
            label_width=84,
        ),
    )


def activity_chart(rows: Sequence[ActivityDay]) -> ChartSpec | None:
    """The home page's server activity (`queries.activity.recent_activity`, oldest first): one bar of sorties per day
    over the last `ACTIVITY_DAYS` days up to the newest day with activity; days without a row are zero. The pilots and
    missions per day are in the table. None without any activity."""
    if not rows:
        return None
    last = rows[-1].day
    by_day = {row.day: row for row in rows}
    days = [last - timedelta(days=back) for back in range(ACTIVITY_DAYS - 1, -1, -1)]
    first = min(row.day for row in rows if row.day >= days[0])  # a young site starts at its first active day
    days = [day for day in days if day >= first]
    sorties: list[int] = []
    pilots: list[int] = []
    missions: list[int] = []
    for day in days:
        row = by_day.get(day)
        sorties.append(row.sorties if row else 0)
        pilots.append(row.pilots if row else 0)
        missions.append(row.missions if row else 0)
    return ChartSpec(
        "chart-activity",
        _("Sorties per day"),
        _("Bar chart of the sorties per day over the last days. The numbers are in the table below."),
        tuple(format_date(day, "j M") for day in days),
        (
            ChartSeries(_("Sorties"), tuple(sorties)),
            ChartSeries(_("Pilots"), tuple(pilots), plotted=False),
            ChartSeries(_("Missions"), tuple(missions), plotted=False),
        ),
    )
