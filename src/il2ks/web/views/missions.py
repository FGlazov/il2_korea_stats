"""Home, mission list and mission detail (FR-WEB-1, FR-WEB-2). Thin: the reads live in `il2ks.queries.missions`.

Hidden missions are absent from the list and the home page and answer 404 on their own page (FR-ADM-3); the
templates anonymise hidden players ("Hidden player", no link).
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from django.core.paginator import Page, Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from il2ks.core.catalog.loader import Side
from il2ks.db.models import PlayerMission, PlayerSortie, SiteSettings, Tour
from il2ks.queries import activity as activity_reads
from il2ks.queries import leaderboards as board_reads
from il2ks.queries import missions as reads
from il2ks.queries.paging import MISSION_PAGE_SIZE, ROW_PAGE_SIZE
from il2ks.queries.tours import is_quiet_tour, tour_choice_from, tour_query
from il2ks.web import columns, display
from il2ks.web.chart_data import activity_chart
from il2ks.web.context_processors import site_row
from il2ks.web.views.leaderboards import BOARD_TITLES

HOME_MISSIONS = 8
HOME_PILOTS = 5
PAGE_SIZE = MISSION_PAGE_SIZE
KILLS_PARAM = "page_kills"
PERIOD_LABELS = {7: _("Last 7 days"), 30: _("Last 30 days"), 90: _("Last 90 days")}


@dataclass(frozen=True, slots=True)
class SideSorties:
    """One coalition's sorties on the mission page: `side` is 'redfor' or 'blufor', None for any other country."""

    side: Side | None
    rows: list[PlayerSortie]
    """This page's rows (`ROW_PAGE_SIZE` at most)."""
    page: Page
    """The Django page the rows come from: `page.paginator.count` is every sortie of the side."""
    page_param: str
    """The query parameter of this table's page number ('page_redfor', 'page_blufor', 'page_other')."""


@dataclass(frozen=True, slots=True)
class TopPilot:
    """A top-pilot row on the home page: the player's mission totals and the side they flew for."""

    row: PlayerMission
    side: Side | None


@dataclass(frozen=True, slots=True)
class HomeBoard:
    """One compact leaderboard on the home page: the board's key (for its link and columns), title and top rows."""

    key: str
    title: str
    rows: list[board_reads.BoardRow]


def home(request: HttpRequest) -> HttpResponse:
    """`/?tour=`: the home page (OQ-79). Like every tour-aware page (TD-26) no `tour` is the current tour and
    `?tour=all` is all time: the last mission, its top pilots, the latest missions, the activity chart, the streak list
    and the compact boards follow the choice (Elo too: it resets every tour); "online now" stays live."""
    choice = tour_choice_from(request.GET)
    latest = reads.latest_missions(HOME_MISSIONS, choice.selected)
    last = latest[0] if latest else None
    pilots = (
        [
            TopPilot(row, display.coalition_side(row.coalition, last.countries))
            for row in reads.top_pilots(last, HOME_PILOTS)
        ]
        if last is not None
        else []
    )
    context: dict[str, object] = {
        "latest": latest,
        "last_mission": last,
        "pilots": pilots,
        "activity": activity_chart(activity_reads.recent_activity(tour=choice.selected)),
        "boards": _home_boards(choice.selected, site_row(request)),
        "tour_query": tour_query(choice.selected),
        **choice.context,
    }
    return render(request, "il2ks/home.html", context)


def _home_boards(tour: Tour | None, site: SiteSettings) -> list[HomeBoard]:
    """The boards the maintainer wants on the home page (OQ-64, OQ-79): Elo of both pools, the skill boards and play
    time, in `tour` (all time without one). One read each."""
    rules = board_reads.rules(site)
    return [
        HomeBoard(key, str(BOARD_TITLES[key]), board_reads.top_rows(board_reads.BOARDS[key], rules, tour=tour))
        for key in board_reads.HOME_BOARDS
    ]


def mission_list(request: HttpRequest) -> HttpResponse:
    sort = reads.resolve_sort(request.GET.get("sort", ""))
    choice = tour_choice_from(request.GET)
    filters = replace(reads.parse_filters(request.GET), tour=choice.selected)
    shown = columns.chosen(request.GET, columns.MISSION_COLUMNS)
    missions = reads.mission_list(filters, sort, datetime.now(UTC), with_tour=any(c.key == "tour" for c in shown))
    page = Paginator(missions, PAGE_SIZE).get_page(request.GET.get("page"))
    context: dict[str, object] = {
        "page_title": _("Missions"),
        "page_obj": page,
        "missions": page.object_list,
        "optional_columns": columns.MISSION_COLUMNS,
        "columns": shown,
        "colspan": 9 + len(shown),
        "quiet_tour": is_quiet_tour(choice.selected, request.GET, page.paginator.count),
        **choice.context,
        "sort": sort,
        "period_options": [(days, label) for days, label in PERIOD_LABELS.items()],
        "winner_options": [("redfor", _("REDFOR won")), ("blufor", _("BLUFOR won")), ("none", _("No winner"))],
        "empty_options": [("1", _("Shown"))],
    }
    return render(request, "il2ks/missions/list.html", context)


def mission_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/missions/<pk>/`. Query parameters: `sort` (one of `reads.SORTIE_SORT_FIELDS`, '-' = descending) orders the
    sortie tables, all three alike, before they are paginated; `cols` adds optional sortie columns
    (`columns.MISSION_SORTIE_COLUMNS`). Each table has its own page number (OQ-96): `page_redfor`, `page_blufor`,
    `page_other` (20 sorties each) and `page_kills` (20 kills); a sort or column change starts them all at page 1.
    The kills table is not sortable and takes neither `sort` nor `cols`. Unknown values are ignored."""
    mission = reads.visible_mission(pk)
    if mission is None:
        raise Http404
    sort = reads.resolve_sortie_sort(request.GET.get("sort", ""))
    shown = columns.chosen(request.GET, columns.MISSION_SORTIE_COLUMNS)
    sorties = reads.mission_sorties(mission, sort)
    groups: list[SideSorties] = []
    for side in ("redfor", "blufor", None):
        param = f"page_{side or 'other'}"
        page = Paginator([row for row in sorties if display.side_of(row.country) == side], ROW_PAGE_SIZE).get_page(
            request.GET.get(param)
        )
        groups.append(SideSorties(side, list(page.object_list), page, param))
    kills = Paginator(reads.mission_kills(mission), ROW_PAGE_SIZE).get_page(request.GET.get(KILLS_PARAM))
    context: dict[str, object] = {
        "page_title": display.mission_title(mission.mission_file),
        "crumbs": [(_("Missions"), reverse("web:mission-list")), (display.mission_title(mission.mission_file), None)],
        "mission": mission,
        "groups": [group for group in groups if group.rows or group.side is not None],
        "kills": kills,
        "kills_param": KILLS_PARAM,
        "sortie_count": len(sorties),
        "sort": sort,
        "optional_columns": columns.MISSION_SORTIE_COLUMNS,
        "columns": shown,
        "colspan": 11 + len(shown),
    }
    return render(request, "il2ks/missions/detail.html", context)
