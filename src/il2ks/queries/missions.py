"""Reads for the home page, the mission list and the mission page (FR-WEB-1, FR-WEB-2, FR-ADM-3).

Plain filters, ORDER BY and LIMIT only (TD-22): every number shown comes from a pre-aggregated column (doc 06). Hidden
missions are never returned (`Mission.objects.visible()`); hidden *players* are returned like any other row and the
template anonymises them ("Hidden player", no link), so a hidden player's sorties still count for the mission.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from django.db.models import QuerySet
from django.http import QueryDict

from il2ks.db.models import HEAVY_SORTIE_COLUMNS, Kill, Mission, PlayerMission, PlayerSortie, Tour
from il2ks.queries.sorting import Ratio, SortSpec, order_by

# What the mission list can be sorted by: ?sort= value -> model field or NULL-safe ratio (the whitelist: anything else
# is ignored). The first block is the default columns, the second the optional ones a visitor can add with `?cols=`
# (`web.columns.MISSION_COLUMNS`; a test keeps the two in step).
SORT_FIELDS: Final[dict[str, SortSpec]] = {
    "date": "started_at",
    "name": "mission_file",
    "duration": "duration_s",
    "players": "players_total",
    "sorties": "sorties_total",
    "air_kills": "kills_air",
    "ground_kills": "kills_ground",
    "friendly_kills": "friendly_kills",
    "tour": "tour__started_at",
    "ended": "ended_at",
    "redfor_sorties": "redfor_sorties",
    "blufor_sorties": "blufor_sorties",
    "sorties_per_player": Ratio("sorties_total", "players_total"),
}
DEFAULT_SORT: Final = "-date"
PERIOD_DAYS: Final = (7, 30, 90)
type Winner = Literal["redfor", "blufor", "none"]
WINNERS: Final[dict[Winner, int | None]] = {"redfor": 1, "blufor": 2, "none": None}  # winner -> winning_coalition


@dataclass(frozen=True, slots=True)
class MissionFilters:
    """The mission list's filters, parsed from the query string (unknown values mean "no filter", never an error)."""

    period_days: int | None = None
    include_empty: bool = False
    winner: Winner | None = None
    name: str = ""
    tour: Tour | None = None  # resolved from `?tour=` by `queries.tours.tour_choice_from`, not by parse_filters


def resolve_sort(raw: str) -> str:
    """The whitelisted `?sort=` ('date', '-kills', ...): an unknown field falls back to newest first."""
    return raw if raw.removeprefix("-") in SORT_FIELDS else DEFAULT_SORT


def parse_filters(params: QueryDict) -> MissionFilters:
    period = params.get("period", "")
    winner = params.get("winner", "")
    return MissionFilters(
        period_days=int(period) if period.isdigit() and int(period) in PERIOD_DAYS else None,
        include_empty=params.get("empty", "") == "1",
        winner=winner if winner in WINNERS else None,
        name=params.get("q", "").strip()[:80],
    )


def mission_list(filters: MissionFilters, sort: str, now: datetime, *, with_tour: bool = False) -> QuerySet[Mission]:
    """Visible missions, filtered and sorted. Empty missions (no sorties) are left out unless asked for. `with_tour`
    loads each mission's tour in the same query (the optional tour column)."""
    missions = Mission.objects.visible()
    if with_tour:
        missions = missions.select_related("tour")
    if not filters.include_empty:
        missions = missions.filter(sorties_total__gt=0)
    if filters.tour is not None:
        missions = missions.filter(tour=filters.tour)
    if filters.period_days is not None:
        missions = missions.filter(started_at__gte=now - timedelta(days=filters.period_days))
    if filters.winner is not None:
        winning = WINNERS[filters.winner]
        missions = (
            missions.filter(winning_coalition__isnull=True)
            if winning is None
            else missions.filter(winning_coalition=winning)
        )
    if filters.name:
        # File names use underscores where the title shows spaces.
        missions = missions.filter(mission_file__icontains=filters.name.replace(" ", "_"))
    return missions.order_by(order_by(SORT_FIELDS[sort.removeprefix("-")], sort), "-started_at", "-pk")


def latest_missions(limit: int) -> list[Mission]:
    """The newest visible missions that have at least one sortie (home page)."""
    return list(Mission.objects.visible().filter(sorties_total__gt=0).order_by("-started_at", "-pk")[:limit])


def top_pilots(mission: Mission, limit: int) -> list[PlayerMission]:
    """The pilots with the most kills in a mission (air first, then ground); hidden players and pilots without a kill
    are left out."""
    rows = (
        PlayerMission.objects.filter(mission=mission, player__is_hidden=False)
        .select_related("player")
        .order_by("-kills_air", "-kills_ground", "player_id")[:limit]
    )
    return [row for row in rows if row.kills_air or row.kills_ground]


def visible_mission(pk: int) -> Mission | None:
    """The mission, or None when it does not exist or is hidden (the page answers 404 for both, FR-ADM-3)."""
    return Mission.objects.visible().filter(pk=pk).first()


def mission_sorties(mission: Mission) -> list[PlayerSortie]:
    """Every sortie of a mission in spawn order, with its player and aircraft (one query)."""
    return list(
        PlayerSortie.objects.filter(mission=mission)
        .select_related("player", "aircraft")
        .defer(*HEAVY_SORTIE_COLUMNS)
        .order_by("spawned_at", "pk")
    )


def mission_kills(mission: Mission) -> list[Kill]:
    """The player-versus-player kills of a mission in time order, with both sorties, players and aircraft."""
    return list(
        Kill.objects.filter(mission=mission)
        .select_related(
            "killer_sortie__player", "killer_sortie__aircraft", "victim_sortie__player", "victim_sortie__aircraft"
        )
        .defer(*(f"{side}__{c}" for side in ("killer_sortie", "victim_sortie") for c in HEAVY_SORTIE_COLUMNS))
        .order_by("tick", "pk")
    )
