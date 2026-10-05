"""Aircraft stats (FR-WEB-8) and hits to destroy (FR-WEB-18). Simple reads from `il2ks.queries` (TD-22).

The numbers cover every counted mission: hiding is presentation only (FR-ADM-3). The detail page names a player only in
the top-pilots table, which leaves hidden players out.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlencode

from django.core.paginator import Page, Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.db.models import AircraftRole
from il2ks.queries import aircraft as reads
from il2ks.queries import ammo as ammo_reads
from il2ks.queries import leaderboards as board_reads
from il2ks.queries import paging
from il2ks.queries.aircraft import StatsRow
from il2ks.queries.players import resolve_sort
from il2ks.queries.tours import aircraft_absence, tour_choice_from
from il2ks.web import columns, display, object_names
from il2ks.web.context_processors import site_row


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
    """An ammo mix (a single ammunition is a mix of one): the ammunition that hit together, in the order the mix is
    stored in, how often it destroyed the type, and the average hits of each ammunition in that order."""

    parts: tuple[MixPart, ...]
    instances: str

    @property
    def averages(self) -> str:
        """`5.4 + 2.7`: the average hits of each ammunition, in the order of `parts`."""
        return " + ".join(part.average for part in self.parts)


@dataclass(frozen=True, slots=True)
class HitsToDestroy:
    """How many gun hits it takes to destroy the type (all gun ammunition together, then per ammunition)."""

    kills: str
    average: str
    by_ammo: tuple[AmmoHits, ...]


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
        parts.append(MixPart(info.name, info.designation, display.num(part.average_hits, 1)))
    return AmmoMixRow(tuple(parts), display.num(mix.instances))


def _hits(found: ammo_reads.AircraftAmmo | None) -> HitsToDestroy:
    if found is None or found.total is None:
        return NO_HITS
    return HitsToDestroy(
        display.num(found.total.kills),
        _average(found.total.average_hits),
        tuple(_ammo_hits(a) for a in found.by_ammo),
    )


def _page[T](rows: Sequence[T], request: HttpRequest, param: str) -> Page:
    """One page of `rows` (the site's page size) for the table that pages by `?param=`."""
    return Paginator(rows, paging.ROW_PAGE_SIZE).get_page(request.GET.get(param, 1))


MIXES_PAGE_PARAM = "page_mixes"  # each table of the page pages apart (`page_*`, see `pagination`)
LOADOUTS_PAGE_PARAM = "page_loadouts"
MODS_PAGE_PARAM = "page_mods"
LOADOUT_ROLE_PARAM = "lrole"  # the loadouts tab under the role "all": `air_superiority` or `attack`


@dataclass(frozen=True, slots=True)
class Tab:
    """One option of the loadouts tab: its label, link and whether it is the shown one."""

    label: str
    href: str
    current: bool


def _loadout_role(request: HttpRequest, role: AircraftRole, attack_first: bool) -> AircraftRole:
    """The combat role the loadouts table shows: the page's own role, else the tab (`?lrole=`), else the role most of
    the type's sorties are flown in."""
    if role != AircraftRole.ALL:
        return role
    chosen = request.GET.get(LOADOUT_ROLE_PARAM)
    if chosen in {AircraftRole.AIR_SUPERIORITY, AircraftRole.ATTACK}:
        return AircraftRole(chosen)
    return AircraftRole.ATTACK if attack_first else AircraftRole.AIR_SUPERIORITY


def _loadout_tabs(request: HttpRequest, shown: AircraftRole) -> tuple[Tab, ...]:
    """Air superiority | Attack links of the loadouts section; they keep every other parameter, a new tab starts at
    page 1 and drops the loadout sort (its column may not exist in the other mode)."""
    params = request.GET.copy()
    for key in (LOADOUT_SORT_PARAM, LOADOUTS_PAGE_PARAM):
        params.pop(key, None)
    tabs: list[Tab] = []
    for value in (AircraftRole.AIR_SUPERIORITY, AircraftRole.ATTACK):
        params[LOADOUT_ROLE_PARAM] = value.value
        tabs.append(Tab(str(display.ROLES[value.value][0]), f"{request.path}?{params.urlencode()}", value == shown))
    return tuple(tabs)


LOADOUT_SORT_PARAM = "lsort"  # the loadouts table sorts apart from the matchups table (`sort`)
MOD_SORT_PARAM = "msort"  # ... and so does the weapon-mods table
INTERCEPT_PARAM = "intercept"  # `?intercept=1`: only fights where both sorties were air superiority


@dataclass(frozen=True, slots=True)
class ModFilter:
    """One significant modification and the visitor's choice: `state` is `any`, `with` or `without`."""

    mod_id: int
    name: str
    state: str


def _query_url(request: HttpRequest, *, intercept: bool) -> str:
    """The page's own URL with the intercept filter set or cleared, keeping the tour and the matchup sort."""
    params: dict[str, str] = {}
    for key, value in request.GET.items():
        if key in {
            "tour",
            "sort",
            reads.ROLE_PARAM,
            LOADOUT_SORT_PARAM,
            MOD_SORT_PARAM,
            LOADOUT_ROLE_PARAM,
        } or key.startswith(reads.MOD_PARAM_PREFIX):
            params[key] = value
    if intercept:
        params[INTERCEPT_PARAM] = "1"
    return f"{request.path}?{urlencode(params)}" if params else request.path


def aircraft_list(request: HttpRequest) -> HttpResponse:
    """`/aircraft/?tour=&sort=`: one row per aircraft type flown in the selected tour (no `tour` = the current tour,
    `?tour=all` = all time, TD-26), with its totals, ratios and the average gun hits it took to destroy it (always all
    time). Sortable (a whitelist in `queries.aircraft`); each row links to the type's page.

    `?role=air_superiority|attack` counts only the sorties of that combat role (default every role).

    Template `il2ks/aircraft/list.html`. Context: rows (`AircraftRow`), sort (resolved), role, tours / tour (the
    selector), page_title, optional_columns (every column a visitor can add) and columns (the ones `?cols=` chose).
    Reads: three queries (the tours, the rows, the hits; plus the 2 of the context processor)."""
    choice = tour_choice_from(request.GET)
    role = reads.parse_role(request.GET.get(reads.ROLE_PARAM))
    sort = resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    destroyed = {a.aircraft_id: a for a in ammo_reads.all_aircraft_ammo()}
    rows = [
        AircraftRow(s, _hits(destroyed.get(s.aircraft_id)), max(s.sorties - s.deaths, 0))
        for s in reads.stats_list(sort, choice.selected, role)
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
        "role": role.value,
        "sort": sort,
        "page_title": _("Aircraft"),
        "optional_columns": columns.AIRCRAFT_COLUMNS,
        "columns": shown,
        "colspan": 13 + len(shown),
    }
    return render(request, "il2ks/aircraft/list.html", context)


def aircraft_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/aircraft/<GameObject pk>/?tour=&role=&intercept=&sort=&lsort=&mod<id>=`: one type. Every section follows every
    filter the visitor sets (maintainer 2026-10-04) and all time stays available (`?tour=all`): the tour (`?tour=` as
    everywhere), the combat role (`?role=air_superiority|attack`, default every role) and, for a type with significant
    weapon modifications, `?mod<id>=with|without` (absent = any). Each section reads the stored rows of that scope
    (TD-22), so each is still one read:

    - the tiles and the pilot count: `TourAircraftStats` (or `AircraftStats` for all time, every role, no filter);
    - loadouts and the modifications table: `AircraftPayload` / `AircraftMods` of the tour, role and filter;
    - matchups: kills and losses against each enemy type, the role and modifications being those of THIS type's sortie
      (the killer's for kills, the victim's for losses), with the intercept toggle (both sorties air superiority);
    - top pilots: per-type Elo (all time by nature) among the pilots with enough air superiority sorties in the scope,
      and ground score per hour on target from the scope's own rows;
    - hits to destroy: one table, a summary row (all counted kills) and one row per ammunition mix (a single ammunition
      is a mix of one) with the average hits of each ammunition in the stored order ("5.4 + 2.7"): the kills OF this
      type, the tour, role and modifications being those of the destroyed aircraft's own sortie (an AI aircraft counts
      for every role without a modification filter only);
    - the mixes, loadouts and modification sets need `paging.MIN_EVENTS_LISTED` kills / sorties to be listed and page
      by `?page_mixes=`, `?page_loadouts=`, `?page_mods=` (20 rows); under the role "all" the loadouts have a tab
      (`?lrole=air_superiority|attack`, default the role most sorties are flown in) that picks the loadouts of one
      role and the columns that fit it; with a single role the table follows it.
    404 for a type nobody flew.

    Template `il2ks/aircraft/detail.html`. Context: stats (`AircraftStats`, all time), tile (the counters the tiles
    show: a `TourAircraftStats` for a scope, or `stats`), aircraft (its GameObject), survived, hits (`HitsToDestroy`:
    the summary row), ammo_mixes (a `Page` of `AmmoMixRow`), min_events, matchups (`queries.aircraft.MatchupTable`),
    sort (the matchup sort), intercept, role ("all", "air_superiority", "attack"), tours / tour (the tour scope),
    min_encounters, all_fights_url / intercept_url, show_elo / show_ground, elo_pilots (`EloRow`s: with .player),
    ground_pilots (`BoardRow`s of the ground-per-hour board), ground_first (an attack type: list ground first), rules
    (the leaderboard minimums), loadouts (a `Page` of `queries.aircraft.Loadout`), loadout_role (the role the table
    shows), loadout_tabs (`Tab`s; empty with a single role), loadout_sort, mod_filters, mod_filtered, mod_sets (a
    `Page`), mod_sort, crumbs, page_title.
    Reads: with a tour, role or filter selected and data in every section, nine queries (the type's all-time row, the
    scope's row, hits, ammo mixes, matchups, loadouts, mod sets, and one query per pilot board: Elo and ground
    both for every role, one for a single role), plus the tours, plus the 2 of the context processor: 12 at most,
    11 with a single role. Without a selection there is no scope row."""
    stats = reads.stats_for(pk)
    if stats is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    role = reads.parse_role(request.GET.get(reads.ROLE_PARAM))
    # An intercept fight is air superiority against air superiority: with the attack role its table is always empty, so
    # the toggle is not offered (`can_intercept`) and a stale `?intercept=1` is ignored.
    can_intercept = role != AircraftRole.ATTACK
    intercept = can_intercept and request.GET.get(INTERCEPT_PARAM) == "1"
    matchup_sort = resolve_sort(
        request.GET.get("sort", ""), dict.fromkeys(reads.MATCHUP_SORTS, ""), reads.DEFAULT_MATCHUP_SORT
    )
    mod_sort = resolve_sort(
        request.GET.get(MOD_SORT_PARAM, ""), dict.fromkeys(reads.MOD_SORTS, ""), reads.DEFAULT_MOD_SORT
    )
    loadout_sort = resolve_sort(
        request.GET.get(LOADOUT_SORT_PARAM, ""), dict.fromkeys(reads.LOADOUT_SORTS, ""), reads.DEFAULT_LOADOUT_SORT
    )
    aircraft = stats.aircraft
    significant = reads.significant_mods(aircraft)
    states = reads.mod_states(request.GET, significant)
    mod_pattern = reads.mod_pattern_of(states)
    tile: StatsRow = (
        stats
        if choice.selected is None and role == AircraftRole.ALL and not mod_pattern
        else reads.scoped_stats(aircraft, choice.selected, role, mod_pattern)
    )
    rules = board_reads.rules(site_row(request))
    name = object_names.name_of(aircraft, get_language() or "en")
    show_elo = role != AircraftRole.ATTACK
    show_ground = role != AircraftRole.AIR_SUPERIORITY
    ground_first = role == AircraftRole.ATTACK or (
        role == AircraftRole.ALL and tile.sorties > 0 and tile.attack_sorties >= ATTACK_TYPE_SHARE * tile.sorties
    )
    min_events = paging.MIN_EVENTS_LISTED
    ammo = ammo_reads.aircraft_ammo(aircraft, choice.selected, role, mod_pattern, min_events)
    hits = _hits(ammo)
    if ammo.total is not None:  # the mix rows show one decimal, so the total row does too
        hits = replace(hits, average=display.num(ammo.total.average_hits, 1))
    loadout_role = _loadout_role(request, role, ground_first)
    context = {
        "page_title": name,
        "crumbs": [(_("Aircraft"), reverse("web:aircraft-list")), (name, None)],
        "stats": stats,
        "tile": tile,
        "aircraft": aircraft,
        "survived": max(tile.sorties - tile.deaths, 0),
        "hits": hits,
        "ammo_mixes": _page([_mix_row(m) for m in ammo.mixes], request, MIXES_PAGE_PARAM),
        "min_events": min_events,
        **choice.context,
        # nobody flew the type in the selected tour (a clean slate): only an empty tile pays the extra read
        "absence": aircraft_absence(aircraft.pk, choice) if tile.sorties == 0 else None,
        "role": role.value,
        "matchups": reads.matchups(aircraft, choice.selected, intercept, matchup_sort, role, mod_pattern),
        "sort": matchup_sort,
        "intercept": intercept,
        "can_intercept": can_intercept,
        "min_encounters": reads.MIN_ENCOUNTERS,
        "all_fights_url": _query_url(request, intercept=False),
        "intercept_url": _query_url(request, intercept=True),
        "show_elo": show_elo,
        "show_ground": show_ground,
        "elo_pilots": reads.top_elo(aircraft, rules, choice.selected, role, mod_pattern) if show_elo else [],
        "ground_pilots": reads.top_ground(aircraft, rules, choice.selected, role, mod_pattern) if show_ground else [],
        "ground_first": ground_first,
        "rules": rules,
        "mod_filters": tuple(ModFilter(m.mod_id, m.name, state) for m, state in zip(significant, states, strict=True)),
        "mod_filtered": bool(mod_pattern),
        "loadouts": _page(
            reads.payloads(aircraft, loadout_role, rules, loadout_sort, mod_pattern, choice.selected, min_events),
            request,
            LOADOUTS_PAGE_PARAM,
        ),
        "loadout_role": loadout_role.value,
        "loadout_tabs": _loadout_tabs(request, loadout_role) if role == AircraftRole.ALL else (),
        "loadout_sort": loadout_sort,
        "mod_sets": _page(
            reads.mod_sets(aircraft, role, rules, mod_sort, mod_pattern, choice.selected, min_events),
            request,
            MODS_PAGE_PARAM,
        ),
        "mod_sort": mod_sort,
    }
    return render(request, "il2ks/aircraft/detail.html", context)
