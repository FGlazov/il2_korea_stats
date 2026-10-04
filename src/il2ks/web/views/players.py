"""Player search and profile (FR-WEB-3, FR-WEB-4). Simple reads from `il2ks.queries.players` (TD-22)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.db.models import Counters
from il2ks.queries import players as reads
from il2ks.queries.stat_marks import stat_thresholds
from il2ks.queries.tours import player_tour, tour_choice_from
from il2ks.web import pve
from il2ks.web.chart_data import player_charts
from il2ks.web.ground import ground_breakdown


def player_search(request: HttpRequest) -> HttpResponse:
    """`/players/?q=&sort=&page=`: search by current or past nickname; the list of recently active players without `q`.

    Template `il2ks/players/search.html`. Context: `q` (the trimmed query), `sort` (resolved, e.g. '-last_seen'),
    `page_obj` (a Django Page of `PlayerHit(player, matched_name)`), `page_title`."""
    query = request.GET.get("q", "").strip()[: reads.MAX_QUERY_LENGTH]
    sort = reads.resolve_sort(request.GET.get("sort", ""), reads.PLAYER_SORTS, reads.DEFAULT_PLAYER_SORT)
    page = reads.player_page(query, sort, request.GET.get("page", 1))
    context = {"page_title": _("Players"), "q": query, "sort": sort, "page_obj": page}
    return render(request, "il2ks/players/search.html", context)


def player_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/`: header, totals and ratios, ground-kill breakdown, hall of shame, per-aircraft table (sortable
    with `?sort=`), the ten latest sorties. 404 for a missing or hidden player (FR-ADM-3).

    Template `il2ks/players/detail.html`. Context: `player`, `names` (PlayerName rows, newest first), `sort` (resolved),
    `aircraft` (PlayerAircraft rows), `survived` (sorties without a death), `ground` (ground_breakdown),
    `gunner_only`, `recent` (PlayerSortie rows with mission and aircraft), `pve_kills` / `pve_losses` (web.pve rows of
    `stats`, FR-WEB-21), `charts` (per-tour ChartSpecs, all tours, FR-WEB-16), `crumbs`, `page_title`.
    With `?tour=<id>` (queries.tours.tour_choice_from; unknown = all time): `tours`, `tour`, and `stats` is the
    PlayerTour row (None when the player flew nothing in that tour) instead of the Player; `marks` (stat thresholds
    of that scope, FR-WEB-22); `aircraft` and `recent` are that tour's."""
    player = reads.visible_player(pk)
    if player is None:
        raise Http404
    sort = reads.resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    choice = tour_choice_from(request.GET)  # `?tour=<id>`; unknown or absent = all time (TD-26)
    tour = choice.selected
    stats: Counters | None = player if tour is None else player_tour(player.pk, tour)
    context: dict[str, object] = {
        **choice.context,
        "stats": stats,
        "marks": stat_thresholds(tour),  # FR-WEB-22: one query
        "page_title": player.current_name,
        "crumbs": [(_("Players"), reverse("web:player-search")), (player.current_name, None)],
        "player": player,
        "names": reads.past_names(player),
        "sort": sort,
        "aircraft": reads.aircraft_rows(player, sort, tour),
        "survived": max(stats.sorties - stats.deaths, 0) if stats else 0,
        "ground": ground_breakdown(stats) if stats else [],
        "pve_kills": pve.kill_breakdown(stats) if stats else [],
        "pve_losses": pve.loss_breakdown(stats) if stats else [],
        "gunner_only": tour is None and reads.flies_as_gunner_only(player),
        "recent": reads.recent_sorties(player, tour=tour),
        "charts": player_charts(reads.tour_history(player)) if player.sorties else (),
    }
    return render(request, "il2ks/players/detail.html", context)
