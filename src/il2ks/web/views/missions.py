"""Home, mission list and mission detail (FR-WEB-1, FR-WEB-2)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from il2ks.db.models import GameObject
from il2ks.web.views.stub import not_built_yet


def home(request: HttpRequest) -> HttpResponse:
    """Placeholder home page. Doubles as the first subject of the simple-reads harness (TD-22)."""
    playable = GameObject.objects.filter(is_playable=True).order_by("display_name")[:50]
    return render(request, "il2ks/home.html", {"playable": playable})


def mission_list(request: HttpRequest) -> HttpResponse:
    return not_built_yet(request, "Missions")


def mission_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return not_built_yet(request, "Mission")
