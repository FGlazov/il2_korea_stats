"""Home, mission list and mission detail (FR-WEB-1, FR-WEB-2). Thin: the reads live in `il2ks.queries.missions`.

Hidden missions are absent from the list and the home page and answer 404 on their own page (FR-ADM-3); the
templates anonymise hidden players ("Hidden player", no link).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from il2ks.core.catalog.loader import Side
from il2ks.db.models import PlayerMission, PlayerSortie
from il2ks.queries import missions as reads
from il2ks.web import display

HOME_MISSIONS = 8
HOME_PILOTS = 5
PAGE_SIZE = 25
PERIOD_LABELS = {7: _("Last 7 days"), 30: _("Last 30 days"), 90: _("Last 90 days")}


@dataclass(frozen=True, slots=True)
class SideSorties:
    """One coalition's sorties on the mission page: `side` is 'redfor' or 'blufor', None for any other country."""

    side: Side | None
    rows: list[PlayerSortie]


@dataclass(frozen=True, slots=True)
class TopPilot:
    """A top-pilot row on the home page: the player's mission totals and the side they flew for."""

    row: PlayerMission
    side: Side | None


def home(request: HttpRequest) -> HttpResponse:
    latest = reads.latest_missions(HOME_MISSIONS)
    last = latest[0] if latest else None
    pilots = (
        [
            TopPilot(row, display.coalition_side(row.coalition, last.countries))
            for row in reads.top_pilots(last, HOME_PILOTS)
        ]
        if last is not None
        else []
    )
    context: dict[str, object] = {"latest": latest, "last_mission": last, "pilots": pilots}
    return render(request, "il2ks/home.html", context)


def mission_list(request: HttpRequest) -> HttpResponse:
    sort = reads.resolve_sort(request.GET.get("sort", ""))
    filters = reads.parse_filters(request.GET)
    page = Paginator(reads.mission_list(filters, sort, datetime.now(UTC)), PAGE_SIZE).get_page(request.GET.get("page"))
    context: dict[str, object] = {
        "page_title": _("Missions"),
        "page_obj": page,
        "missions": page.object_list,
        "sort": sort,
        "period_options": [(days, label) for days, label in PERIOD_LABELS.items()],
        "winner_options": [("redfor", _("REDFOR won")), ("blufor", _("BLUFOR won")), ("none", _("No winner"))],
        "empty_options": [("1", _("Shown"))],
    }
    return render(request, "il2ks/missions/list.html", context)


def mission_detail(request: HttpRequest, pk: int) -> HttpResponse:
    mission = reads.visible_mission(pk)
    if mission is None:
        raise Http404
    sorties = reads.mission_sorties(mission)
    groups = [
        SideSorties(side, [row for row in sorties if display.side_of(row.country) == side])
        for side in ("redfor", "blufor", None)
    ]
    context: dict[str, object] = {
        "page_title": display.mission_title(mission.mission_file),
        "crumbs": [(_("Missions"), reverse("web:mission-list")), (display.mission_title(mission.mission_file), None)],
        "mission": mission,
        "groups": [group for group in groups if group.rows or group.side is not None],
        "kills": reads.mission_kills(mission),
        "sortie_count": len(sorties),
    }
    return render(request, "il2ks/missions/detail.html", context)
