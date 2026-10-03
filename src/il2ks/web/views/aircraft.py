"""Hits to destroy per aircraft type (FR-WEB-18). One read (`queries.ammo.all_aircraft_ammo`), TD-22.

The numbers cover every counted mission: hiding is presentation only (FR-ADM-3), and the page names no player.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from il2ks.db.models import GameObject
from il2ks.queries import ammo as reads
from il2ks.web import display
from il2ks.web.sortie_view import ammo_name


@dataclass(frozen=True, slots=True)
class AmmoHits:
    """One gun ammunition: in how many counted kills it hit, and the average number of hits in those kills."""

    name: str
    kills: str
    average: str


@dataclass(frozen=True, slots=True)
class AircraftRow:
    aircraft: GameObject
    kills: str
    average: str  # all gun ammunition together
    by_ammo: Sequence[AmmoHits]


def _average(value: float) -> str:
    return display.num(value, 1 if value >= 10 else 2)


def aircraft_list(request: HttpRequest) -> HttpResponse:
    """`/aircraft/`: every aircraft type shot down by gun hits in a kill with a single attacker, most killed first, with
    the average hits (all gun ammunition, then per ammunition) it took to destroy it.

    Template `il2ks/aircraft/list.html`. Context: rows (`AircraftRow`), page_title.
    Reads: one query (plus the 2 of the context processor)."""
    rows = [
        AircraftRow(
            row.aircraft,
            display.num(row.total.kills) if row.total else display.DASH,
            _average(row.total.average_hits) if row.total else display.DASH,
            [AmmoHits(ammo_name(a.ammo), display.num(a.kills), _average(a.average_hits)) for a in row.by_ammo],
        )
        for row in reads.all_aircraft_ammo()
    ]
    return render(request, "il2ks/aircraft/list.html", {"rows": rows, "page_title": _("Aircraft")})
