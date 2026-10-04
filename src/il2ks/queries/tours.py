"""Tour reads for the pages (TD-26, FR-WEB-10): simple reads only (TD-22), no aggregation.

How a page uses them (the `?tour=<id>` convention; no `tour` parameter or `tour=` means all time):

    choice = tour_choice_from(request.GET)                # TourChoice(tours, selected), one query
    context = {**choice.context, ...}                     # "tours" and "tour" for {% tour_select tours tour %}
    stats = choice.selected and player_tour(player.pk, choice.selected)   # PlayerTour or None -> all-time `Player`
    aircraft = player_tour_aircraft(player.pk, choice.selected) if choice.selected else player.aircraft_stats...

and in the template `{% tour_select tours tour %}` (a select with "All time", see `components/tour_select.html`) or,
inside a `{% filter_bar %}`, `{% filter_select "tour" _("Tour") tour_options all_label=_("All time") %}` with
`tour_options = tour_options(choice.tours)`.

Mission lists filter with `Mission.objects.visible().filter(tour=selected)`. Hidden players and missions are the
page's job as everywhere else (`Player.objects.visible()`); `PlayerTour` rows of hidden players are not filtered here.
Titles are localised at display time (`tour_title`, the `tour_title` filter): never show `Tour.title` raw.
Elo is all-time only (`Player.elo_*`): don't offer it per tour.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from django.db.models import QuerySet
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from il2ks.core.tours import MONTH_NAMES
from il2ks.db.models import PlayerTour, PlayerTourAircraft, Tour

TOUR_PARAM = "tour"  # the one query parameter every tour-aware page uses: `?tour=<Tour.pk>`, absent = all time
_MONTHLY_TITLE = re.compile(rf"^({'|'.join(MONTH_NAMES)}) (\d{{4}})$")
_DAYS_TITLE = re.compile(r"^Tour (\d+)$")


@dataclass(frozen=True, slots=True)
class TourChoice:
    tours: list[Tour]  # newest first, for the selector
    selected: Tour | None  # None = all time

    @property
    def context(self) -> dict[str, object]:
        """The template context `{% tour_select tours tour %}` needs: `tours` and `tour` (the chosen one or None)."""
        return {"tours": self.tours, "tour": self.selected}


def current_tour(now: datetime | None = None) -> Tour | None:
    """The tour a visitor expects to see first: the newest tour that has started (the open one in manual mode, the
    current calendar period once it has a mission, else the latest earlier one). None before any mission."""
    moment = now or datetime.now(UTC)
    return Tour.objects.filter(started_at__lte=moment).order_by("-started_at").first()


def list_tours() -> list[Tour]:
    """All tours, newest first (the selector's options). One tour per month or per N days: a short list."""
    return list(Tour.objects.order_by("-started_at"))


def tour_title(title: str) -> str:
    """The tour's title in the viewer's language. Automatic titles are stored in English ("October 2026", "Tour 7",
    `core.tours`) and localised here at display time; any other title is an admin's rename (FR-ADM-8), shown as is."""
    month = _MONTHLY_TITLE.match(title)
    if month:
        return date_format(date(int(month[2]), MONTH_NAMES.index(month[1]) + 1, 1), "YEAR_MONTH_FORMAT")
    number = _DAYS_TITLE.match(title)
    if number:
        return _("Tour %(number)s") % {"number": number[1]}
    return title


def tour_options(tours: list[Tour]) -> list[tuple[int, str]]:
    """(id, localised title) pairs for `{% filter_select %}`."""
    return [(tour.pk, tour_title(tour.title)) for tour in tours]


def tour_choice(raw: str | None) -> TourChoice:
    """The selector state for `?tour=<raw>`: an unknown, malformed or empty value means all time (never an error:
    a stale shared link still shows a page). One query. Prefer `tour_choice_from` in views."""
    tours = list_tours()
    wanted = int(raw) if raw is not None and raw.isdecimal() else None
    selected = next((tour for tour in tours if tour.pk == wanted), None)
    return TourChoice(tours, selected)


def tour_choice_from(params: Mapping[str, str]) -> TourChoice:
    """`tour_choice` for a request's query string: `tour_choice_from(request.GET)`."""
    return tour_choice(params.get(TOUR_PARAM))


def player_tour(player_id: int, tour: Tour) -> PlayerTour | None:
    """The player's counters in that tour, None when they flew no counted sortie in it."""
    return PlayerTour.objects.filter(player_id=player_id, tour=tour).first()


def player_tour_aircraft(player_id: int, tour: Tour) -> QuerySet[PlayerTourAircraft]:
    """The player's per-aircraft counters in that tour (the profile's aircraft table), aircraft pre-loaded."""
    return PlayerTourAircraft.objects.filter(player_id=player_id, tour=tour).select_related("aircraft")


def tour_leaderboard(tour: Tour) -> QuerySet[PlayerTour]:
    """The tour's player rows of visible players, players pre-loaded; the page orders and paginates them."""
    return PlayerTour.objects.filter(tour=tour, player__is_hidden=False).select_related("player")
