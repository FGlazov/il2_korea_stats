"""The full killboard and the best streaks of a player, and the list of running ironman streaks (FR-WEB-9, FR-WEB-23).

Simple reads from `il2ks.queries.boards` (TD-22). A hidden player has no killboard (404, FR-ADM-3) and is never listed.
"""

from datetime import UTC, datetime

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.core.streaks import MIN_LISTED_RUN
from il2ks.queries import boards as reads
from il2ks.queries import players as player_reads
from il2ks.queries.tours import tour_choice_from, tour_query


def player_killboard(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/killboard/?tour=&sort=&page=`: every opponent the player has shot down or been shot down by,
    all-time or in the selected tour (TD-26).

    Template `il2ks/players/killboard.html`. Context: `player`, `tours`, `tour`, `page_obj` (PlayerKillboard or
    PlayerTourKillboard rows with `opponent` and `last_mission`), `sort` (resolved), `crumbs`, `page_title`.
    The assists column shows when `site.killboard_assists` is on."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    sort = player_reads.resolve_sort(request.GET.get("sort", ""), reads.KILLBOARD_SORTS, reads.DEFAULT_KILLBOARD_SORT)
    choice = tour_choice_from(request.GET)
    context = {
        **choice.context,
        "page_title": _("%(name)s: killboard") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Killboard"), None),
        ],
        "player": player,
        "sort": sort,
        "page_obj": reads.killboard_page(player, sort, request.GET.get("page", 1), choice.selected),
    }
    return render(request, "il2ks/players/killboard.html", context)


def player_streaks(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/streaks/?tour=`: the player's best ironman streaks, by sorties survived, air kills and flight
    time, all-time or in the selected tour (a streak within a tour stays inside it).

    Template `il2ks/players/streaks.html`. Context: `player`, `tours`, `tour`, `streaks` (PlayerBestStreak rows in the
    order sorties, air kills, flight time; a kind may be missing), `crumbs`, `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    context = {
        **choice.context,
        "page_title": _("%(name)s: best streaks") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Best streaks"), None),
        ],
        "player": player,
        "streaks": reads.best_streaks(player, choice.selected),
    }
    return render(request, "il2ks/players/streaks.html", context)


def streak_list(request: HttpRequest) -> HttpResponse:
    """`/streaks/?tour=&sort=&page_best=&page_running=`: every pilot's best ironman streak in the selected tour (or all
    time), plus the streaks running right now (TD-26, FR-WEB-23).

    Template `il2ks/streaks/list.html`. Context: `tours`, `tour`, `best_page` (PlayerBestStreak rows with `player`),
    `running_page` (PlayerStreak rows with `player`; None on a past tour, where nothing is running [PROPOSED]), `sort`
    (of the running list), `page_title`, `active_days`."""
    sort = player_reads.resolve_sort(request.GET.get("sort", ""), reads.STREAK_SORTS, reads.DEFAULT_STREAK_SORT)
    choice = tour_choice_from(request.GET)
    past_tour = choice.selected is not None and choice.selected != choice.current
    context = {
        **choice.context,
        "page_title": _("Ironman streaks"),
        "sort": sort,
        "best_page": reads.best_streaks_page(choice.selected, request.GET.get("page_best", 1)),
        "running_page": None
        if past_tour
        else reads.streak_page(sort, request.GET.get("page_running", 1), datetime.now(UTC)),
        "active_days": reads.ACTIVE_DAYS,
    }
    return render(request, "il2ks/streaks/list.html", context)


def player_streak_runs(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/streaks/history/?tour=&page=`: every streak of the player (a run of at least two survived
    sorties, finished or running), newest first, 20 per page, all-time or within the selected tour (OQ-82).

    Template `il2ks/players/streak_runs.html`. Context: `player`, `tours`, `tour`, `page_obj` (PlayerStreakRun rows with
    `ended_sortie` and its mission), `min_run`, `crumbs`, `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    context = {
        **choice.context,
        "page_title": _("%(name)s: all streaks") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Best streaks"), reverse("web:player-streaks", args=[player.pk]) + tour_query(choice.selected)),
            (_("All streaks"), None),
        ],
        "player": player,
        "min_run": MIN_LISTED_RUN,
        "page_obj": reads.streak_runs_page(player, request.GET.get("page", 1), choice.selected),
    }
    return render(request, "il2ks/players/streak_runs.html", context)
