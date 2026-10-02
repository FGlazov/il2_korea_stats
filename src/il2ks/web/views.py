from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from il2ks.db.models import GameObject


def home(request: HttpRequest) -> HttpResponse:
    """Placeholder home page. Doubles as the first subject of the simple-reads harness (TD-22)."""
    playable = GameObject.objects.filter(is_playable=True).order_by("display_name")[:50]
    return render(request, "il2ks/home.html", {"playable": playable})
