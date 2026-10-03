"""A player's sortie list and the sortie detail page (FR-WEB-5, FR-WEB-6)."""

from django.http import HttpRequest, HttpResponse

from il2ks.web.views.stub import not_built_yet


def player_sorties(request: HttpRequest, pk: int) -> HttpResponse:
    return not_built_yet(request, "Sorties")


def sortie_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return not_built_yet(request, "Sortie")
