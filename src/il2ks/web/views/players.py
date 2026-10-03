"""Player search and profile (FR-WEB-3, FR-WEB-4)."""

from django.http import HttpRequest, HttpResponse

from il2ks.web.views.stub import not_built_yet


def player_search(request: HttpRequest) -> HttpResponse:
    return not_built_yet(request, "Players")


def player_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return not_built_yet(request, "Player")
