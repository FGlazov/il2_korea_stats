"""The full killboard and the best streaks of a player, and the old streak list URL (FR-WEB-9, FR-WEB-23).

Simple reads from `il2ks.queries.boards` (TD-22). A hidden player has no killboard (404, FR-ADM-3) and is never listed.
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _

from il2ks.core.streaks import MIN_LISTED_RUN
from il2ks.queries import boards as reads
from il2ks.queries import players as player_reads
from il2ks.queries.tours import pilot_absence, tour_choice_from, tour_query


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
    page = reads.killboard_page(player, sort, request.GET.get("page", 1), choice.selected)
    context = {
        **choice.context,
        "absence": pilot_absence(player.pk, choice) if page.paginator.count == 0 else None,
        "page_title": _("%(name)s: killboard") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Killboard"), None),
        ],
        "player": player,
        "sort": sort,
        "page_obj": page,
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
    streaks = reads.best_streaks(player, choice.selected)
    context = {
        **choice.context,
        "absence": None if streaks else pilot_absence(player.pk, choice),
        "page_title": _("%(name)s: best streaks") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Best streaks"), None),
        ],
        "player": player,
        "streaks": streaks,
    }
    return render(request, "il2ks/players/streaks.html", context)


def streak_list(request: HttpRequest) -> HttpResponse:
    """`/streaks/?tour=`: the old list of ironman streaks, now the ironman board of the leaderboards (maintainer
    2026-10-05); old links keep working and land on the air board, in the same tour."""
    query = f"?{urlencode({'tour': request.GET['tour']})}" if request.GET.get("tour") else ""
    return redirect(reverse("web:leaderboard", args=["ironman-all"]) + query, permanent=True)


def _track_url(request: HttpRequest, track: str) -> str:
    """This page's URL for another ironman track: the other parameters (the tour) stay, paging starts over."""
    params = {key: value for key, value in request.GET.items() if key not in ("track", "page")}
    return f"{request.path}?{urlencode({**params, 'track': track})}"


def player_streak_runs(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/streaks/history/?tour=&page=`: every streak of the player on one track
    (`?track=all|air|ground`, default all; a run of at least two survived sorties, finished or running), newest first,
    20 per page, all-time or within the selected tour (OQ-82).

    Template `il2ks/players/streak_runs.html`. Context: `player`, `tours`, `tour`, `page_obj` (PlayerStreakRun rows with
    `ended_sortie` and its mission), `track` (all, air or ground, `?track=`), `track_options` ((label, url, current)
    rows), `tour_ended` (the selected tour is not the current one), `min_run`, `crumbs`,
    `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    track = request.GET.get("track", "")
    track = track if track in reads.TRACKS else reads.TRACKS[0]
    page = reads.streak_runs_page(player, request.GET.get("page", 1), choice.selected, track)
    context = {
        **choice.context,
        "track": track,
        "track_options": [
            (label, _track_url(request, value), value == track)
            for value, label in zip(reads.TRACKS, (_("All"), _("Air"), _("Ground")), strict=True)
        ],
        "absence": pilot_absence(player.pk, choice) if page.paginator.count == 0 else None,
        "page_title": _("%(name)s: all streaks") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Best streaks"), reverse("web:player-streaks", args=[player.pk]) + tour_query(choice.selected)),
            (_("All streaks"), None),
        ],
        "player": player,
        "min_run": MIN_LISTED_RUN,
        # an open run of a finished tour did not "still go": the tour ran out (decided from data, TD-28)
        "tour_ended": choice.selected is not None and choice.selected != choice.current,
        "page_obj": page,
    }
    return render(request, "il2ks/players/streak_runs.html", context)
