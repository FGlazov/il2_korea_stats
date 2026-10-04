"""Aircraft stats (FR-WEB-8) and hits to destroy (FR-WEB-18). Simple reads from `il2ks.queries` (TD-22).

The numbers cover every counted mission: hiding is presentation only (FR-ADM-3). The detail page names a player only in
the top-pilots table, which leaves hidden players out.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.queries import aircraft as reads
from il2ks.queries import ammo as ammo_reads
from il2ks.queries import leaderboards as board_reads
from il2ks.queries.aircraft import StatsRow
from il2ks.queries.players import resolve_sort
from il2ks.queries.tours import tour_choice_from
from il2ks.web import columns, display, object_names


@dataclass(frozen=True, slots=True)
class AmmoHits:
    """One gun ammunition: in how many counted kills it hit, and the average number of hits in those kills."""

    name: str  # plain name (`.50 BMG API`)
    designation: str  # real designation, for a tooltip
    kills: str
    average: str


@dataclass(frozen=True, slots=True)
class MixPart:
    """One ammunition of a mix with its average hits in the mix's instances."""

    name: str
    designation: str
    average: str


@dataclass(frozen=True, slots=True)
class AmmoMixRow:
    """An ammo mix: the ammunition that hit together, how often it destroyed the type, average hits per ammunition."""

    parts: tuple[MixPart, ...]
    instances: str
    average: str  # all hits of the mix together


MIX_ROWS_SHOWN = 10
"""Ammo mixes listed before the "show all" fold (the rest sits in a `<details>`)."""


@dataclass(frozen=True, slots=True)
class HitsToDestroy:
    """How many gun hits it takes to destroy the type (all gun ammunition together, then per ammunition)."""

    kills: str
    average: str
    by_ammo: tuple[AmmoHits, ...]
    mixes: tuple[AmmoMixRow, ...] = ()
    more_mixes: tuple[AmmoMixRow, ...] = ()  # beyond `MIX_ROWS_SHOWN`


@dataclass(frozen=True, slots=True)
class AircraftRow:
    stats: StatsRow  # `AircraftStats` (all time) or `TourAircraftStats` (the selected tour)
    hits: HitsToDestroy
    survived: int  # sorties without a death


ATTACK_TYPE_SHARE = 0.5
"""A type with at least this share of attack sorties lists its ground proficiency ranking before its Elo."""
NO_HITS = HitsToDestroy(display.DASH, display.DASH, ())


def _average(value: float) -> str:
    return display.num(value, 1 if value >= 10 else 2)


def _ammo_hits(row: ammo_reads.AmmoToDestroy) -> AmmoHits:
    info = ammo_reads.ammo_info(row.ammo)
    return AmmoHits(info.name, info.designation, display.num(row.kills), _average(row.average_hits))


def _mix_row(mix: ammo_reads.AmmoMix) -> AmmoMixRow:
    parts: list[MixPart] = []
    for part in mix.parts:
        info = ammo_reads.ammo_info(part.ammo)
        parts.append(MixPart(info.name, info.designation, _average(part.average_hits)))
    return AmmoMixRow(tuple(parts), display.num(mix.instances), _average(mix.average_hits))


def _hits(found: ammo_reads.AircraftAmmo | None) -> HitsToDestroy:
    if found is None or found.total is None:
        return NO_HITS
    return HitsToDestroy(
        display.num(found.total.kills),
        _average(found.total.average_hits),
        tuple(_ammo_hits(a) for a in found.by_ammo),
        tuple(_mix_row(m) for m in found.mixes[:MIX_ROWS_SHOWN]),
        tuple(_mix_row(m) for m in found.mixes[MIX_ROWS_SHOWN:]),
    )


INTERCEPT_PARAM = "intercept"  # `?intercept=1`: only fights where both sorties were air superiority


def _query_url(request: HttpRequest, *, intercept: bool) -> str:
    """The page's own URL with the intercept filter set or cleared, keeping the tour and the matchup sort."""
    params: dict[str, str] = {}
    for key in ("tour", "sort"):
        value = request.GET.get(key)
        if value is not None:
            params[key] = value
    if intercept:
        params[INTERCEPT_PARAM] = "1"
    return f"{request.path}?{urlencode(params)}" if params else request.path


def aircraft_list(request: HttpRequest) -> HttpResponse:
    """`/aircraft/?tour=&sort=`: one row per aircraft type flown in the selected tour (no `tour` = the current tour,
    `?tour=all` = all time, TD-26), with its totals, ratios and the average gun hits it took to destroy it (always all
    time). Sortable (a whitelist in `queries.aircraft`); each row links to the type's page.

    Template `il2ks/aircraft/list.html`. Context: rows (`AircraftRow`), sort (resolved), tours / tour (the selector),
    page_title, optional_columns (every column a visitor can add) and columns (the ones `?cols=` chose).
    Reads: three queries (the tours, the rows, the hits; plus the 2 of the context processor)."""
    choice = tour_choice_from(request.GET)
    sort = resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    destroyed = {a.aircraft_id: a for a in ammo_reads.all_aircraft_ammo()}
    rows = [
        AircraftRow(s, _hits(destroyed.get(s.aircraft_id)), max(s.sorties - s.deaths, 0))
        for s in reads.stats_list(sort, choice.selected)
    ]
    if sort.removeprefix("-") == "aircraft":
        # The database orders by the English `display_name`; the page shows the localized name, so sort by that
        # (a few dozen rows; the stable sort keeps the database's tie order).
        language = get_language() or "en"
        rows.sort(
            key=lambda r: object_names.name_of(r.stats.aircraft, language).casefold(), reverse=sort.startswith("-")
        )
    shown = columns.chosen(request.GET, columns.AIRCRAFT_COLUMNS)
    context = {
        "rows": rows,
        **choice.context,
        "sort": sort,
        "page_title": _("Aircraft"),
        "optional_columns": columns.AIRCRAFT_COLUMNS,
        "columns": shown,
        "colspan": 13 + len(shown),
    }
    return render(request, "il2ks/aircraft/list.html", context)


def aircraft_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/aircraft/<GameObject pk>/`: the type's totals and ratios (in the selected tour, `?tour=` as everywhere; the
    tiles are zero when nobody flew it there), hits to destroy per ammunition, matchups against
    each enemy type, its top pilots by skill (per-type Elo, and ground score per hour on target; visible players
    who meet the leaderboard minimums) and common loadouts. 404 for a type nobody flew.

    Template `il2ks/aircraft/detail.html`. Context: stats (`AircraftStats`, all time), tile (the counters the tiles
    show: the tour's `TourAircraftStats`, or `stats`), aircraft (its GameObject), survived,
    hits (`HitsToDestroy`), matchups (`queries.aircraft.MatchupTable`: rows, best, worst), sort (the matchup sort),
    intercept, tours / tour (the matchup scope), min_encounters, all_fights_url / intercept_url,
    elo_pilots (`PlayerAircraft` rows with .player),
    ground_pilots (`BoardRow`s of the ground-per-hour board), ground_first (an attack type: list ground first), rules
    (the leaderboard minimums), payloads (`AircraftPayload`), crumbs, page_title.
    Reads: seven queries plus the tours (eight with a tour selected) (plus the 2 of the context processor)."""
    stats = reads.stats_for(pk)
    if stats is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    intercept = request.GET.get(INTERCEPT_PARAM) == "1"
    matchup_sort = resolve_sort(
        request.GET.get("sort", ""), dict.fromkeys(reads.MATCHUP_SORTS, ""), reads.DEFAULT_MATCHUP_SORT
    )
    aircraft = stats.aircraft
    tile: StatsRow = reads.tour_stats_for(aircraft, choice.selected) if choice.selected else stats
    rules = board_reads.rules()
    name = object_names.name_of(aircraft, get_language() or "en")
    context = {
        "page_title": name,
        "crumbs": [(_("Aircraft"), reverse("web:aircraft-list")), (name, None)],
        "stats": stats,
        "tile": tile,
        "aircraft": aircraft,
        "survived": max(tile.sorties - tile.deaths, 0),
        "hits": _hits(ammo_reads.aircraft_ammo(aircraft)),
        **choice.context,
        "matchups": reads.matchups(aircraft, choice.selected, intercept, matchup_sort),
        "sort": matchup_sort,
        "intercept": intercept,
        "min_encounters": reads.MIN_ENCOUNTERS,
        "all_fights_url": _query_url(request, intercept=False),
        "intercept_url": _query_url(request, intercept=True),
        "elo_pilots": reads.top_elo(aircraft, rules),
        "ground_pilots": reads.top_ground(aircraft, rules),
        "ground_first": stats.sorties > 0 and stats.attack_sorties >= ATTACK_TYPE_SHARE * stats.sorties,
        "rules": rules,
        "payloads": reads.payloads(aircraft),
    }
    return render(request, "il2ks/aircraft/detail.html", context)
