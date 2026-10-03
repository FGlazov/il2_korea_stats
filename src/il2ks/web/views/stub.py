"""Placeholder for pages that are being built (removed once every page exists)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def not_built_yet(request: HttpRequest, title: str) -> HttpResponse:
    return render(request, "il2ks/stub.html", {"title": title})
