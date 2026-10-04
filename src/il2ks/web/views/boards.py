"""The full killboard of a player and the list of running ironman streaks (FR-WEB-9, FR-WEB-23).

Simple reads from `il2ks.queries.boards` (TD-22). A hidden player has no killboard (404, FR-ADM-3) and is never listed.
"""

from datetime import UTC, datetime

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.queries import boards as reads
from il2ks.queries import players as player_reads


def player_killboard(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/killboard/?sort=&page=`: every opponent the player has shot down or been shot down by.

    Template `il2ks/players/killboard.html`. Context: `player`, `page_obj` (PlayerKillboard rows with `opponent` and
    `last_mission`), `sort` (resolved), `crumbs`, `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    sort = player_reads.resolve_sort(request.GET.get("sort", ""), reads.KILLBOARD_SORTS, reads.DEFAULT_KILLBOARD_SORT)
    context = {
        "page_title": _("%(name)s: killboard") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk])),
            (_("Killboard"), None),
        ],
        "player": player,
        "sort": sort,
        "page_obj": reads.killboard_page(player, sort, request.GET.get("page", 1)),
    }
    return render(request, "il2ks/players/killboard.html", context)


def streak_list(request: HttpRequest) -> HttpResponse:
    """`/streaks/?sort=&page=`: players on a running streak of survived sorties (Ironman).

    Template `il2ks/streaks/list.html`. Context: `page_obj` (PlayerStreak rows with `player`), `sort`, `page_title`,
    `active_days`."""
    sort = player_reads.resolve_sort(request.GET.get("sort", ""), reads.STREAK_SORTS, reads.DEFAULT_STREAK_SORT)
    context = {
        "page_title": _("Ironman streaks"),
        "sort": sort,
        "page_obj": reads.streak_page(sort, request.GET.get("page", 1), datetime.now(UTC)),
        "active_days": reads.ACTIVE_DAYS,
    }
    return render(request, "il2ks/streaks/list.html", context)
