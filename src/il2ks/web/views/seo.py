"""`/sitemap.xml` and `/robots.txt`: so a pilot who searches for their nickname finds the server.

The sitemap lists the pages worth finding: the main lists, every aircraft type, every visible mission that has sorties
(the mission list's own rule) and every visible player. Hidden players and hidden missions never appear (FR-ADM-3), and
neither do sortie pages or filtered or paginated variants. The admin's Markdown pages (`/p/<slug>/`) are listed
with the main pages (`/sitemap-site.xml`). URLs are absolute, from
the configured public address (`[https] domain`, `settings.IL2KS_PUBLIC_URL`); with no domain set they take the host the
request came in on.

Size: one sitemap file holds at most `PAGE_SIZE` URLs (the protocol allows 50,000; smaller files are cheaper to build
and to fetch). When everything fits, `/sitemap.xml` is that file. Otherwise it is a sitemap index pointing to
`/sitemap-site.xml` (lists and aircraft), `/sitemap-missions-<n>.xml` and `/sitemap-players-<n>.xml`. A file reads only
its own slice of rows (ordered by primary key, two columns each); the index costs one COUNT per kind.

Cached by the data version like every other page: `web.caching` answers a matching `If-None-Match` before the view runs,
and a rendered file is kept in memory until the data version changes (a crawler asking for 100 files after one import
builds each once). Only when `IL2KS_PUBLIC_URL` is set: without it the addresses come from the Host header, which
any client chooses, so a cache keyed by it would grow with every made-up Host (review 0.2.0 M8); those requests
render every time (ETag revalidation still answers a repeat visitor with a 304).
"""

import math
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Literal
from xml.sax.saxutils import escape

from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from il2ks.db.models import AircraftStats, Mission, Page, Player
from il2ks.db.site import current_data_version

PAGE_SIZE = 10_000
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

STATIC_PAGES = (
    "web:home",
    "web:mission-list",
    "web:leaderboards",
    "web:player-search",
    "web:aircraft-list",
    "web:achievements",
    "web:streak-list",
    "web:sortie-list",
)

type Kind = Literal["missions", "players"]
type Entry = tuple[str, datetime | None]  # (path, last change)

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], str] = {}
_cache_version: int | None = None


def _remembered(version: int, key: tuple[str, str], build: Callable[[], str]) -> str:
    """The rendered file for this data version; a new version forgets everything older. Not kept (just built) while no
    public address is configured: the key would hold a client-chosen Host."""
    global _cache_version
    if not settings.IL2KS_PUBLIC_URL:
        return build()
    with _cache_lock:
        if _cache_version != version:
            _cache.clear()
            _cache_version = version
        found = _cache.get(key)
    if found is not None:
        return found
    text = build()
    with _cache_lock:
        if _cache_version == version:
            _cache[key] = text
    return text


def base_url(request: HttpRequest) -> str:
    """`https://domain[:port]` from the configuration; else the address this request came in on."""
    configured: str = settings.IL2KS_PUBLIC_URL
    return configured or request.build_absolute_uri("/").rstrip("/")


def _urlset(base: str, entries: Iterable[Entry]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<urlset xmlns="{SITEMAP_NS}">']
    for where, changed in entries:
        stamp = f"<lastmod>{changed.date().isoformat()}</lastmod>" if changed is not None else ""
        lines.append(f"<url><loc>{escape(base + where)}</loc>{stamp}</url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _index(base: str, files: Iterable[str]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<sitemapindex xmlns="{SITEMAP_NS}">']
    lines += [f"<sitemap><loc>{escape(base + name)}</loc></sitemap>" for name in files]
    lines.append("</sitemapindex>")
    return "\n".join(lines) + "\n"


# --- what each file holds ------------------------------------------------------------------------------------------


def _visible(kind: Kind) -> QuerySet[Mission] | QuerySet[Player]:
    if kind == "missions":
        return Mission.objects.visible().filter(sorties_total__gt=0)  # the mission list's own rule
    return Player.objects.visible()


def _count(kind: Kind) -> int:
    return _visible(kind).count()


def _files(count: int) -> int:
    return max(1, math.ceil(count / PAGE_SIZE))


def _site_entries() -> list[Entry]:
    pages: list[Entry] = [(reverse(name), None) for name in STATIC_PAGES]
    aircraft = AircraftStats.objects.order_by("aircraft_id").values_list("aircraft_id", flat=True)
    aircraft_pages: list[Entry] = [(reverse("web:aircraft-detail", args=[pk]), None) for pk in aircraft]
    written = Page.objects.order_by("slug").values_list("slug", "updated_at")
    return pages + aircraft_pages + [(reverse("web:page", args=[slug]), changed) for slug, changed in written]


def _entries(kind: Kind, number: int) -> list[Entry]:
    """File `number` (from 1) of `kind`: its slice of rows by primary key."""
    start = (number - 1) * PAGE_SIZE
    if kind == "missions":
        mission_rows = (
            Mission.objects.visible()
            .filter(sorties_total__gt=0)
            .order_by("pk")
            .values_list("pk", "ended_at")[start : start + PAGE_SIZE]
        )
        return [(reverse("web:mission-detail", args=[pk]), changed) for pk, changed in mission_rows]
    player_rows = Player.objects.visible().order_by("pk").values_list("pk", "last_seen")[start : start + PAGE_SIZE]
    return [(reverse("web:player-detail", args=[pk]), changed) for pk, changed in player_rows]


# --- views ---------------------------------------------------------------------------------------------------------


def _xml(body: str) -> HttpResponse:
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@require_safe
def sitemap_index(request: HttpRequest) -> HttpResponse:
    """`/sitemap.xml`: the whole sitemap when it fits one file, else the index of the files."""
    base = base_url(request)

    def build() -> str:
        missions, players = _count("missions"), _count("players")
        site = _site_entries()
        if len(site) + missions + players <= PAGE_SIZE:
            return _urlset(base, site + _entries("missions", 1) + _entries("players", 1))
        names = ["/sitemap-site.xml"]
        names += [f"/sitemap-missions-{n}.xml" for n in range(1, _files(missions) + 1)]
        names += [f"/sitemap-players-{n}.xml" for n in range(1, _files(players) + 1)]
        return _index(base, names)

    return _xml(_remembered(current_data_version(), ("index", base), build))


@require_safe
def sitemap_site(request: HttpRequest) -> HttpResponse:
    base = base_url(request)
    return _xml(_remembered(current_data_version(), ("site", base), lambda: _urlset(base, _site_entries())))


@require_safe
def sitemap_part(request: HttpRequest, kind: Kind, number: int) -> HttpResponse:
    """`/sitemap-<kind>-<n>.xml` (kind: missions or players, n from 1)."""
    if number < 1 or number > _files(_count(kind)):
        raise Http404
    base = base_url(request)
    key = (f"{kind}-{number}", base)
    return _xml(_remembered(current_data_version(), key, lambda: _urlset(base, _entries(kind, number))))


DISALLOWED = (
    "/admin/",
    "/setup/",
    "/live/",  # HTMX fragment
    "/language/",  # the language switcher's redirect
    "/*?*sort=",  # filter and sort variants of a page repeat its content
    "/*?*cols=",
    "/*?*q=",
    "/*?*page",
)


@require_safe
def robots_txt(request: HttpRequest) -> HttpResponse:
    """`/robots.txt`: public pages allowed, the admin, fragments and filter variants not, and the sitemap's address."""
    base = base_url(request)
    lines = ["User-agent: *", *[f"Disallow: {rule}" for rule in DISALLOWED], "", f"Sitemap: {base}/sitemap.xml"]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
