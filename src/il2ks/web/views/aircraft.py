"""Aircraft stats (FR-WEB-8) and hits to destroy (FR-WEB-18). Simple reads from `il2ks.queries` (TD-22).

The numbers cover every counted mission: hiding is presentation only (FR-ADM-3). The detail page names a player only in
the top-pilots table, which leaves hidden players out.
"""

from dataclasses import dataclass

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.db.models import AircraftStats
from il2ks.queries import aircraft as reads
from il2ks.queries import ammo as ammo_reads
from il2ks.queries.players import resolve_sort
from il2ks.web import display, object_names
from il2ks.web.sortie_view import ammo_name


@dataclass(frozen=True, slots=True)
class AmmoHits:
    """One gun ammunition: in how many counted kills it hit, and the average number of hits in those kills."""

    name: str
    kills: str
    average: str


@dataclass(frozen=True, slots=True)
class HitsToDestroy:
    """How many gun hits it takes to destroy the type (all gun ammunition together, then per ammunition)."""

    kills: str
    average: str
    by_ammo: tuple[AmmoHits, ...]


@dataclass(frozen=True, slots=True)
class AircraftRow:
    stats: AircraftStats
    hits: HitsToDestroy
    survived: int  # sorties without a death


NO_HITS = HitsToDestroy(display.DASH, display.DASH, ())


def _average(value: float) -> str:
    return display.num(value, 1 if value >= 10 else 2)


def _hits(found: ammo_reads.AircraftAmmo | None) -> HitsToDestroy:
    if found is None or found.total is None:
        return NO_HITS
    return HitsToDestroy(
        display.num(found.total.kills),
        _average(found.total.average_hits),
        tuple(AmmoHits(ammo_name(a.ammo), display.num(a.kills), _average(a.average_hits)) for a in found.by_ammo),
    )


def aircraft_list(request: HttpRequest) -> HttpResponse:
    """`/aircraft/?sort=`: one row per aircraft type flown, with its totals, ratios and the average gun hits it took to
    destroy it. Sortable (a whitelist in `queries.aircraft`); each row links to the type's page.

    Template `il2ks/aircraft/list.html`. Context: rows (`AircraftRow`), sort (resolved), page_title.
    Reads: two queries (plus the 2 of the context processor)."""
    sort = resolve_sort(request.GET.get("sort", ""), reads.AIRCRAFT_SORTS, reads.DEFAULT_AIRCRAFT_SORT)
    destroyed = {a.aircraft_id: a for a in ammo_reads.all_aircraft_ammo()}
    rows = [
        AircraftRow(s, _hits(destroyed.get(s.aircraft_id)), max(s.sorties - s.deaths, 0))
        for s in reads.stats_list(sort)
    ]
    return render(request, "il2ks/aircraft/list.html", {"rows": rows, "sort": sort, "page_title": _("Aircraft")})


def aircraft_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/aircraft/<GameObject pk>/`: the type's totals and ratios, hits to destroy per ammunition, matchups against
    each enemy type, top pilots (visible players with enough sorties) and common loadouts. 404 for a type nobody flew.

    Template `il2ks/aircraft/detail.html`. Context: stats (`AircraftStats`), aircraft (its GameObject), survived,
    hits (`HitsToDestroy`), matchups (`queries.aircraft.Matchup`), pilots (`PlayerAircraft` rows with .player),
    min_sorties, payloads (`AircraftPayload`), crumbs, page_title.
    Reads: six queries (plus the 2 of the context processor)."""
    stats = reads.stats_for(pk)
    if stats is None:
        raise Http404
    aircraft = stats.aircraft
    name = object_names.name_of(aircraft, get_language() or "en")
    context = {
        "page_title": name,
        "crumbs": [(_("Aircraft"), reverse("web:aircraft-list")), (name, None)],
        "stats": stats,
        "aircraft": aircraft,
        "survived": max(stats.sorties - stats.deaths, 0),
        "hits": _hits(ammo_reads.aircraft_ammo(aircraft)),
        "matchups": reads.matchups(aircraft),
        "pilots": reads.top_pilots(aircraft),
        "min_sorties": reads.MIN_PILOT_SORTIES,
        "payloads": reads.payloads(aircraft),
    }
    return render(request, "il2ks/aircraft/detail.html", context)
