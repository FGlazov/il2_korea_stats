"""Tour reads for the pages (TD-26, FR-WEB-10): simple reads only (TD-22), no aggregation.

How a page uses them (the `?tour=<id>` convention; no `tour` parameter or `tour=` means all time):

    choice = tour_choice(request.GET.get("tour"))         # TourChoice(tours, selected)
    context = {"tours": choice.tours, "tour": choice.selected, ...}
    stats = choice.selected and player_tour(player.pk, choice.selected)   # PlayerTour or None -> all-time `Player`
    aircraft = player_tour_aircraft(player.pk, choice.selected) if choice.selected else player.aircraft_stats...

and in the template `{% tour_select tours tour %}` (a select with "All time", see `components/tour_select.html`) or,
inside a `{% filter_bar %}`, `{% filter_select "tour" _("Tour") tour_options all_label=_("All time") %}` with
`tour_options = tour_options(choice.tours)`.

Mission lists filter with `Mission.objects.visible().filter(tour=selected)`. Hidden players and missions are the
page's job as everywhere else (`Player.objects.visible()`); `PlayerTour` rows of hidden players are not filtered here.
Elo is all-time only (`Player.elo_*`): don't offer it per tour.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.db.models import QuerySet

from il2ks.db.models import PlayerTour, PlayerTourAircraft, Tour


@dataclass(frozen=True, slots=True)
class TourChoice:
    tours: list[Tour]  # newest first, for the selector
    selected: Tour | None  # None = all time


def current_tour(now: datetime | None = None) -> Tour | None:
    """The tour a visitor expects to see first: the newest tour that has started (the open one in manual mode, the
    current calendar period once it has a mission, else the latest earlier one). None before any mission."""
    moment = now or datetime.now(UTC)
    return Tour.objects.filter(started_at__lte=moment).order_by("-started_at").first()


def list_tours() -> list[Tour]:
    """All tours, newest first (the selector's options). One tour per month or per N days: a short list."""
    return list(Tour.objects.order_by("-started_at"))


def tour_options(tours: list[Tour]) -> list[tuple[int, str]]:
    """(id, title) pairs for `{% filter_select %}`."""
    return [(tour.pk, tour.title) for tour in tours]


def tour_choice(raw: str | None) -> TourChoice:
    """The selector state for `?tour=<raw>`: an unknown, malformed or empty value means all time (never an error)."""
    tours = list_tours()
    wanted = int(raw) if raw is not None and raw.isdecimal() else None
    selected = next((tour for tour in tours if tour.pk == wanted), None)
    return TourChoice(tours, selected)


def player_tour(player_id: int, tour: Tour) -> PlayerTour | None:
    """The player's counters in that tour, None when they flew no counted sortie in it."""
    return PlayerTour.objects.filter(player_id=player_id, tour=tour).first()


def player_tour_aircraft(player_id: int, tour: Tour) -> QuerySet[PlayerTourAircraft]:
    """The player's per-aircraft counters in that tour (the profile's aircraft table), aircraft pre-loaded."""
    return PlayerTourAircraft.objects.filter(player_id=player_id, tour=tour).select_related("aircraft")


def tour_leaderboard(tour: Tour) -> QuerySet[PlayerTour]:
    """The tour's player rows of visible players, players pre-loaded; the page orders and paginates them."""
    return PlayerTour.objects.filter(tour=tour, player__is_hidden=False).select_related("player")
