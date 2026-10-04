"""Leaderboards (FR-WEB-7, FR-WEB-19, FR-WEB-20): the air-to-air Elo of the jet and prop pools, air score, ground score
per hour on target and ground score. Thin: the reads live in `il2ks.queries.leaderboards` (TD-22).

`/leaderboards/` shows the air score board, `/leaderboards/<board>/` any of them (`?tour=`, `?aircraft=`, `?pool=`,
`?sort=`, `?page=`); the retired `/leaderboards/kills/` redirects permanently to the index. The board switcher lists the
air boards, then the ground boards, and keeps the tour / pool / aircraft choice where the target board has the filter.
The score boards can be split into propeller and jet pilots with `?pool=`. Hidden players are never listed
(FR-ADM-3)."""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from il2ks.queries import leaderboards as reads
from il2ks.queries import tours as tour_reads
from il2ks.web import object_names

BOARD_TITLES = {
    "air": _("Air score"),
    "ground": _("Ground score"),
    "ground-hour": _("Ground score per hour"),
    "interception": _("Interception"),
    "tank-busting": _("Tank busting"),
    "elo-prop": _("Elo, prop"),
    "elo-jet": _("Elo, jet"),
}
GROUP_TITLES = {
    "air": _("Air"),
    "ground": _("Ground"),
}
BOARD_ICONS = {  # the icon set (static/il2ks/img): chess pieces for the Elo boards, the roles for the scores
    "elo-jet": "stat/elo-jet",
    "elo-prop": "stat/elo-prop",
    "air": "role/air-superiority",
    "interception": "stat/interception",
    "ground-hour": "stat/ground-hour",
    "tank-busting": "ground/tank",
    "ground": "role/attack",
}
RETIRED_BOARDS = frozenset({"kills"})  # removed 2026-10-04; old links go to the index
POOL_OPTIONS = (("prop", _("Propeller")), ("jet", _("Jet")))
BOARD_HELP = {
    "air": _("Points for air kills and assists, minus penalties for deaths, lost aircraft and friendly kills."),
    "ground": _("Points for ground kills (a tank counts for far more than a fence), minus the same penalties."),
    "ground-hour": _(
        "Ground score earned in attack sorties per hour spent on target. Transit to and from the target is not counted."
    ),
    "interception": _(
        "Bombers and attackers shot down per hour of air superiority flight: AI bombers and attackers, and player "
        "aircraft flying an attack sortie (bombs or rockets). Only air superiority sorties count."
    ),
    "tank-busting": _(
        "Tanks destroyed in attack sorties per hour spent on target. Transit to and from the target is not counted."
    ),
    "elo-prop": _(
        "Air-to-air Elo of propeller aircraft, from kills between air superiority sorties. All time. "
        "A propeller kill on a jet counts double."
    ),
    "elo-jet": _("Air-to-air Elo of jets, from kills between air superiority sorties. All time."),
}


def _tab_url(key: str, request: HttpRequest) -> str:
    """The URL of a board's tab: keeps the tour, propulsion and aircraft choice where that board has the filter (the
    Elo boards are all-time only and have neither). Sorting and paging start over."""
    target = reads.BOARDS[key]
    keep = {
        name: value
        for name, value, supported in (
            ("tour", request.GET.get("tour", ""), target.per_tour),
            ("pool", request.GET.get("pool", ""), target.per_pool),
            ("aircraft", request.GET.get("aircraft", ""), target.per_aircraft),
        )
        if supported and value
    }
    query = f"?{urlencode(keep)}" if keep else ""
    return reverse("web:leaderboard", args=[key]) + query


def leaderboard(request: HttpRequest, board: str = reads.DEFAULT_BOARD) -> HttpResponse:
    """`/leaderboards/[<board>/]?tour=&aircraft=&pool=&sort=&page=`. 404 for an unknown board.

    Template `il2ks/leaderboards/list.html`. Context: `board` (the Board), `title`, `help`, `tabs` ((key, title, url,
    current, icon) rows), `tab_groups` ((group title, tabs) pairs: air, ground), `pool_options` ((value,
    label) pairs for the prop / jet filter, empty for the Elo boards), `sort` (resolved), `page_obj` (`BoardRow` page),
    `tours` / `tour` (the tour selector state; empty and None for all time and for boards without tours),
    `aircraft_options` ((pk, name) pairs, empty for boards without an aircraft filter), `rules` (the
    minimum-activity thresholds), `page_title`."""
    if board in RETIRED_BOARDS:
        return redirect("web:leaderboards", permanent=True)
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
    tabs = [(key, BOARD_TITLES[key], _tab_url(key, request), key == board, BOARD_ICONS[key]) for key in reads.BOARDS]
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
        "tour": choice.selected if choice else None,
        "aircraft_options": [(a.pk, object_names.name_of(a, language)) for a in planes],
        "rules": rules,
    }
    return render(request, "il2ks/leaderboards/list.html", context)
