"""Player search and profile (FR-WEB-3, FR-WEB-4). Simple reads from `il2ks.queries.players` (TD-22)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.db.models import AircraftRole, Counters
from il2ks.queries import players as reads
from il2ks.queries.stat_marks import stat_thresholds
from il2ks.queries.tours import pilot_absence, player_tour, tour_choice_from, tour_query
from il2ks.web import columns, pve
from il2ks.web.chart_data import player_charts
from il2ks.web.ground import ground_breakdown
from il2ks.web.views.aircraft import ATTACK_TYPE_SHARE


def player_search(request: HttpRequest) -> HttpResponse:
    """`/players/?q=&sort=&page=`: search by current or past nickname; the list of recently active players without `q`.

    Template `il2ks/players/search.html`. Context: `q` (the trimmed query), `sort` (resolved, e.g. '-last_seen'),
    `page_obj` (a Django Page of `PlayerHit(player, matched_name)`), `page_title`, `optional_columns` (every column a
    visitor can add) and `columns` (the ones `?cols=` chose, in that order; all-time values from the Player row)."""
    query = request.GET.get("q", "").strip()[: reads.MAX_QUERY_LENGTH]
    sort = reads.resolve_sort(request.GET.get("sort", ""), reads.PLAYER_SORTS, reads.DEFAULT_PLAYER_SORT)
    page = reads.player_page(query, sort, request.GET.get("page", 1))
    shown = columns.chosen(request.GET, columns.PLAYER_COLUMNS)
    context = {
        "page_title": _("Players"),
        "q": query,
        "sort": sort,
        "page_obj": page,
        "optional_columns": columns.PLAYER_COLUMNS,
        "columns": shown,
        "colspan": 7 + len(shown),
    }
    return render(request, "il2ks/players/search.html", context)


def _air_active(elo: reads.EloShown, stats: Counters | None) -> bool:
    """Whether the air-to-air part has anything to show: air kills, air assists or a rated Elo game (in the scope)."""
    if stats is None:
        return False
    return bool(stats.kills_air or stats.assists_air or elo.elo_prop_games or elo.elo_jet_games)


def _ground_active(stats: Counters | None) -> bool:
    """Whether the air-to-ground part has anything to show: ground kills or assists, attack sorties or ground score."""
    if stats is None:
        return False
    return bool(stats.kills_ground or stats.assists_ground or stats.attack_sorties or stats.score_ground)


def player_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/`: header, general tiles, hall of shame, the five latest sorties, then the air-to-air part, the
    air-to-ground part and an overall part (streaks, per-aircraft table sortable with `?sort=`, PvE, totals, charts).
    404 for a missing or hidden player (FR-ADM-3).

    Template `il2ks/players/detail.html`. Context: `player`, `names` (PlayerName rows, newest first), `sort` (resolved),
    `aircraft` (PlayerAircraft rows), `survived` (sorties without a death), `ground` (ground_breakdown),
    `gunner_only`, `air_active` / `ground_active` (the part has anything to show in this scope; else it collapses to a
    line), `recent` (PlayerSortie rows with mission and aircraft), `elo` (queries.players.EloShown of the scope),
    `pve_kills` / `pve_losses` (web.pve rows of
    `stats`, FR-WEB-21), `charts` (per-tour ChartSpecs, all tours, FR-WEB-16), `crumbs`, `page_title`.
    `?role=air_superiority|attack` (default every role, like the aircraft pages): `role` ("all", "air_superiority",
    "attack"), `has_sorties` (the pilot has sorties in the tour scope whatever the role: the toggle and the no-sortie
    notice follow it), and `stats`, `aircraft`, `survived`, `ground`, `pve_*` and `recent` count only that role's
    sorties (the `PlayerRole` row and the `PlayerAircraftScope` rows of that role; a role view skips the medals, the
    Elo of attack and the charts, so it reads fewer rows than the all-roles page). `show_elo` (false for attack: Elo
    is air-to-air), `ground_first` (the air-to-ground part comes before the air-to-air one: the attack role, or every
    role when at least half the pilot's sorties are attack).
    With `?tour=<id>` (queries.tours.tour_choice_from; unknown = all time): `tours`, `tour`, and `stats` is the
    PlayerTour row (None when the player flew nothing in that tour) instead of the Player; `marks` (stat thresholds
    of that scope, FR-WEB-22); `aircraft` and `recent` are that tour's."""
    player = reads.visible_player(pk)
    if player is None:
        raise Http404
    sort = reads.resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    choice = tour_choice_from(request.GET)  # `?tour=<id>`; unknown or absent = all time (TD-26)
    tour = choice.selected
    role = reads.parse_role(request.GET.get(reads.ROLE_PARAM))
    base: Counters | None = player if tour is None else player_tour(player.pk, tour)  # every role
    history = reads.tour_history(player) if player.sorties and role == AircraftRole.ALL else []  # no per-role charts
    absence = pilot_absence(player.pk, choice) if base is None else None  # every tour he flew in, not the charts' 12
    elo = reads.elo_shown(player, tour) if role != AircraftRole.ATTACK else reads.EloShown()
    stats: Counters | None
    if role == AircraftRole.ALL or base is None:
        stats, aircraft = base, reads.aircraft_rows(player, sort, tour)
    else:  # the per-role copies of the player rows (PlayerRole) and of the aircraft rows (PlayerAircraftScope)
        stats, aircraft = reads.role_counters(player, tour, role), reads.role_aircraft_rows(player, sort, tour, role)
    ground_first = role == AircraftRole.ATTACK or (
        role == AircraftRole.ALL and bool(stats) and stats.attack_sorties >= ATTACK_TYPE_SHARE * max(stats.sorties, 1)
    )
    context: dict[str, object] = {
        **choice.context,
        "tour_query": tour_query(tour),  # keeps the scope on the links to the achievement pages
        "role": role.value,
        "has_sorties": bool(base and base.sorties),
        "show_elo": role != AircraftRole.ATTACK,
        "ground_first": ground_first,
        "stats": stats,
        "marks": stat_thresholds(tour),  # FR-WEB-22: one query
        "page_title": player.current_name,
        "crumbs": [(_("Players"), reverse("web:player-search")), (player.current_name, None)],
        "player": player,
        "names": reads.past_names(player),
        "sort": sort,
        "aircraft": aircraft,
        "elo": elo,  # all time: the best tour's rating; in a tour: its final rating (OQ-128)
        "air_active": _air_active(elo, stats),
        "ground_active": _ground_active(stats),
        "survived": max(stats.sorties - stats.deaths, 0) if stats else 0,
        "ground": ground_breakdown(stats) if stats else [],
        "pve_kills": pve.kill_breakdown(stats) if stats else [],
        "pve_losses": pve.loss_breakdown(stats) if stats else [],
        "gunner_only": reads.flies_as_gunner_only(player),  # all-time counters, so it holds in any tour view
        "recent": reads.recent_sorties(player, tour=tour, role=role),
        "charts": player_charts(history) if player.sorties else (),
        "absence": absence,  # the pilot flew nothing in the selected tour: offer All time and the tours he flew in
    }
    return render(request, "il2ks/players/detail.html", context)
