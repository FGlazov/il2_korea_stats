"""Tour reads for the pages (TD-26, FR-WEB-10): simple reads only (TD-22), no aggregation.

How a page uses them (the `?tour=<id>` convention, TD-26): no `tour` parameter (or an unknown value) means the
**current tour** (`current_tour_of`: the newest tour row, so it changes only when the data does and cached pages stay
valid, TD-28); `?tour=all` (`TOUR_ALL`) is the explicit all-time view; `?tour=<id>` is that tour.

    choice = tour_choice_from(request.GET)                # TourChoice(tours, selected, ...), one query
    context = {**choice.context, ...}                     # "tours", "tour" for {% tour_select %}
    stats = choice.selected and player_tour(player.pk, choice.selected)   # PlayerTour or None -> all-time `Player`
    aircraft = player_tour_aircraft(player.pk, choice.selected) if choice.selected else player.aircraft_stats...

and in the template `{% tour_select tours tour %}` (a select plus the all-time toggle,
see `components/tour_select.html`)
or, inside a `{% filter_bar %}`, `{% tour_filter tours tour %}` (the same select and toggle without a form).

Mission lists filter with `Mission.objects.visible().filter(tour=selected)`. Hidden players and missions are the
page's job as everywhere else (`Player.objects.visible()`); `PlayerTour` rows of hidden players are not filtered here.
Titles are localised at display time (`tour_title`, the `tour_title` filter): never show `Tour.title` raw.
Elo is all-time only (`Player.elo_*`): don't offer it per tour.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from django.db.models import QuerySet
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from il2ks.core.tours import MONTH_NAMES
from il2ks.db.models import PlayerTour, PlayerTourAircraft, Tour, TourAircraftStats

TOUR_PARAM = "tour"  # the one query parameter every tour-aware page uses: `?tour=<Tour.pk>`, absent = current tour
MAX_TOUR_DIGITS = 18  # longer ids are unknown (fits a 64-bit pk, and keeps int() away from 4300+ digit strings)
TOUR_ALL = "all"  # `?tour=all`: the explicit all-time view
_MONTHLY_TITLE = re.compile(rf"^({'|'.join(MONTH_NAMES)}) (\d{{4}})$")
_DAYS_TITLE = re.compile(r"^Tour (\d+)$")


@dataclass(frozen=True, slots=True)
class TourChoice:
    tours: list[Tour]  # newest first, for the selector
    selected: Tour | None  # None = all time (`?tour=all`, or no tour exists yet)

    @property
    def current(self) -> Tour | None:
        """The tour pages open on when `?tour` is absent: the newest one (see `current_tour_of`)."""
        return current_tour_of(self.tours)

    @property
    def context(self) -> dict[str, object]:
        """The template context the tour components need: `tours`, `tour` (the chosen one or None for all time)."""
        return {"tours": self.tours, "tour": self.selected}


def current_tour(now: datetime | None = None) -> Tour | None:
    """The tour a visitor expects to see first: the newest tour that has started (the open one in manual mode, the
    current calendar period once it has a mission, else the latest earlier one). None before any mission."""
    moment = now or datetime.now(UTC)
    return Tour.objects.filter(started_at__lte=moment).order_by("-started_at").first()


def current_tour_of(tours: list[Tour]) -> Tour | None:
    """The default tour of a page: the newest tour row of `tours` (newest first). It depends on the data only, never
    on the request time, so a page's bytes stay a function of the data version (TD-28). The price: the first day of a
    new month still opens on last month until the first mission of the new tour is ingested."""
    return tours[0] if tours else None


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
    """(id, localised title) pairs, newest first."""
    return [(tour.pk, tour_title(tour.title)) for tour in tours]


def tour_query(selected: Tour | None) -> str:
    """The query string that keeps a page's tour scope on a link: `?tour=<id>`, or `?tour=all` for all time (a bare
    link would mean the current tour)."""
    return tour_id_query(selected.pk if selected else None)


def tour_id_query(tour_id: int | None) -> str:
    """`tour_query` for a tour id (a mission's `tour_id`; None = no tour = all time): how a page about one mission or
    sortie links to its pilots, so they open in the tour they flew in, not in whatever the current tour is."""
    return f"?{TOUR_PARAM}={tour_id if tour_id is not None else TOUR_ALL}"


def tour_choice(raw: str | None) -> TourChoice:
    """The selector state for `?tour=<raw>`: `all` is all time, a tour id that exists is that tour, anything else
    (absent, empty, malformed, a stale shared link) is the current tour: never an error, a link always shows a page.
    One query. Prefer `tour_choice_from` in views."""
    tours = list_tours()
    if raw == TOUR_ALL:
        return TourChoice(tours, None)
    # isdecimal() also accepts non-ASCII digits, and int() of thousands of digits raises: cap the length first
    wanted = int(raw) if raw is not None and raw.isdecimal() and len(raw) <= MAX_TOUR_DIGITS else None
    selected = next((tour for tour in tours if tour.pk == wanted), None)
    return TourChoice(tours, selected or current_tour_of(tours))


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


@dataclass(frozen=True, slots=True)
class TourAbsence:
    """A pilot or an aircraft type with no rows in the selected tour (a new tour is a clean slate: whoever did not
    fly in it is simply absent, never an error). `flown` are the tours it does have rows in, newest first: where the
    page offers to go instead (together with all time)."""

    tour: Tour
    flown: list[Tour]


def _absence(selected: Tour | None, tours: list[Tour], flown_ids: set[int]) -> TourAbsence | None:
    if selected is None or selected.pk in flown_ids:
        return None
    return TourAbsence(selected, [tour for tour in tours if tour.pk in flown_ids])


def pilot_absence(player_id: int, choice: TourChoice) -> TourAbsence | None:
    """Whether the pilot has no counted sortie in the selected tour (None for all time, or when he has): one query, so
    call it only once a page came up empty."""
    if choice.selected is None:
        return None
    flown = set(PlayerTour.objects.filter(player_id=player_id).values_list("tour_id", flat=True))
    return _absence(choice.selected, choice.tours, flown)


def absence_from_history(selected: Tour | None, tours: list[Tour], history: Iterable[PlayerTour]) -> TourAbsence | None:
    """`pilot_absence` from the player's `PlayerTour` rows a page already loaded (the profile's tour history)."""
    return _absence(selected, tours, {row.tour_id for row in history})


def aircraft_absence(aircraft_id: int, choice: TourChoice) -> TourAbsence | None:
    """Whether nobody flew the aircraft type in the selected tour (None for all time, or when somebody did): one query,
    from the unfiltered all-roles rows. Call it only once the page's tiles came up empty."""
    if choice.selected is None:
        return None
    rows = TourAircraftStats.objects.filter(aircraft_id=aircraft_id, tour__isnull=False, role="all", mod_pattern="")
    return _absence(choice.selected, choice.tours, set(rows.values_list("tour_id", flat=True)))


def is_quiet_tour(selected: Tour | None, params: Mapping[str, str], rows: int) -> bool:
    """Whether a list page explains an empty result with the empty-tour flavor text (FR-WEB-23): a tour is picked,
    nothing is listed and no other filter is active (so the tour itself is the reason).
    Paging parameters (`page`, `page_*`) are not filters."""
    ignored = {TOUR_PARAM, "page", "sort", "cols"}
    others = (value for key, value in params.items() if key not in ignored and not key.startswith("page_"))
    return selected is not None and rows == 0 and not any(others)
