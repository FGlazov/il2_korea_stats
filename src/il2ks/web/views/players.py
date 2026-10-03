"""Player search and profile (FR-WEB-3, FR-WEB-4). Simple reads from `il2ks.queries.players` (TD-22)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.queries import players as reads
from il2ks.web import pve
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
    `pve_kills` / `pve_losses` (web.pve rows, FR-WEB-21), `gunner_only`,
    `recent` (PlayerSortie rows with mission and aircraft), `crumbs`, `page_title`."""
    player = reads.visible_player(pk)
    if player is None:
        raise Http404
    sort = reads.resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    context = {
        "page_title": player.current_name,
        "crumbs": [(_("Players"), reverse("web:player-search")), (player.current_name, None)],
        "player": player,
        "names": reads.past_names(player),
        "sort": sort,
        "aircraft": reads.aircraft_rows(player, sort),
        "survived": max(player.sorties - player.deaths, 0),
        "ground": ground_breakdown(player),
        "pve_kills": pve.kill_breakdown(player),
        "pve_losses": pve.loss_breakdown(player),
        "gunner_only": reads.flies_as_gunner_only(player),
        "recent": reads.recent_sorties(player),
    }
    return render(request, "il2ks/players/detail.html", context)
