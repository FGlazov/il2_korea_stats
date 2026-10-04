"""Leaderboards (FR-WEB-7, FR-WEB-19, FR-WEB-20): air score, ground score, ground score per hour on target, kills and
the air-to-air Elo of the prop and jet pools. Thin: the reads live in `il2ks.queries.leaderboards` (TD-22).

`/leaderboards/` shows the air score board, `/leaderboards/<board>/` any of them (`?tour=`, `?aircraft=`, `?pool=`,
`?sort=`, `?page=`). The tabs are grouped into fighter boards, attack boards and the general kills board, and the
score boards can be split into propeller and jet pilots with `?pool=`. Hidden players are never listed (FR-ADM-3)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from il2ks.queries import leaderboards as reads
from il2ks.queries import tours as tour_reads
from il2ks.web import object_names

BOARD_TITLES = {
    "air": _("Air score"),
    "ground": _("Ground score"),
    "ground-hour": _("Ground score per hour"),
    "kills": _("Kills"),
    "elo-prop": _("Elo, prop"),
    "elo-jet": _("Elo, jet"),
}
GROUP_TITLES = {
    "fighter": _("Fighters"),
    "attack": _("Attack"),
    "general": _("General"),
}
POOL_OPTIONS = (("prop", _("Propeller")), ("jet", _("Jet")))
BOARD_HELP = {
    "air": _("Points for air kills and assists, minus penalties for deaths, lost aircraft and friendly kills."),
    "ground": _("Points for ground kills (a tank counts for far more than a fence), minus the same penalties."),
    "ground-hour": _(
        "Ground score earned in attack sorties per hour spent on target. Transit to and from the target is not counted."
    ),
    "kills": _("Air and ground kills."),
    "elo-prop": _(
        "Air-to-air Elo of propeller aircraft, from kills between air superiority sorties. All time. "
        "A propeller kill on a jet counts double."
    ),
    "elo-jet": _("Air-to-air Elo of jets, from kills between air superiority sorties. All time."),
}


def leaderboard(request: HttpRequest, board: str = reads.DEFAULT_BOARD) -> HttpResponse:
    """`/leaderboards/[<board>/]?tour=&aircraft=&pool=&sort=&page=`. 404 for an unknown board.

    Template `il2ks/leaderboards/list.html`. Context: `board` (the Board), `title`, `help`, `tabs` ((key, title, url,
    current) rows), `tab_groups` ((group title, tabs) pairs: fighter, attack, general), `pool_options` ((value, label)
    pairs for the prop / jet filter, empty for the Elo boards), `sort` (resolved), `page_obj` (Page of `BoardRow`),
    `tours` / `tour` (the tour selector state; empty and None for all time and for boards without tours),
    `tour_options`, `aircraft_options` ((pk, name) pairs, empty for boards without an aircraft filter), `rules` (the
    minimum-activity thresholds), `page_title`."""
    spec = reads.BOARDS.get(board)
    if spec is None:
        raise Http404
    rules = reads.rules()
    sort = reads.resolve_sort(spec, request.GET.get("sort", ""))
    choice = tour_reads.tour_choice(request.GET.get("tour")) if spec.per_tour else None
    planes = reads.aircraft_options() if spec.per_aircraft else []
    aircraft = reads.parse_aircraft(request.GET.get("aircraft"), planes) if spec.per_aircraft else None
    pool = reads.parse_pool(request.GET.get("pool"), spec)
    selected_tour = choice.selected if choice else None
    page = reads.board_page(spec, sort, request.GET.get("page"), rules, selected_tour, aircraft, pool)
    language = get_language() or "en"
    tabs = [(key, BOARD_TITLES[key], reverse("web:leaderboard", args=[key]), key == board) for key in reads.BOARDS]
    context: dict[str, object] = {
        "page_title": BOARD_TITLES[board],
        "board": spec,
        "title": BOARD_TITLES[board],
        "help": BOARD_HELP[board],
        "tabs": tabs,
        "tab_groups": [(GROUP_TITLES[g], [t for t in tabs if reads.BOARDS[t[0]].group == g]) for g in reads.GROUPS],
        "pool_options": list(POOL_OPTIONS) if spec.per_pool else [],
        "sort": sort,
        "page_obj": page,
        "tours": choice.tours if choice else [],
        "tour_options": tour_reads.tour_options(choice.tours) if choice else [],
        "tour": choice.selected if choice else None,
        "aircraft_options": [(a.pk, object_names.name_of(a, language)) for a in planes],
        "rules": rules,
    }
    return render(request, "il2ks/leaderboards/list.html", context)
