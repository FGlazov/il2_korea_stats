"""Server-side page cache (TD-28 step 2): the rendered list pages, keyed on their ETag, plus a warmer that renders
them again after each data change.

**What is cached**: the views in `CACHED_VIEWS`: the front page, the mission, player and sortie lists, the aircraft
list and each aircraft's page, and the leaderboards; in every variant a visitor asks for (query string, language, HTMX
request). Detail pages of a mission, player or sortie are not: there are too many of them, and each is rarely read
twice in a short time. Neither are name searches (`?q=`), nor requests that carry a session cookie (a logged-in admin).

**Key**: the ETag `web.caching` already builds (data version, language, il2ks version, process start, full path with
query, `HX-Request`) plus the Host. Whatever makes a 304 right makes the cached body right: a mission save, a rebuild
and every admin save of something the pages show bump the data version, and a restart (a new `custom/` template)
changes the start ID. Old entries are simply never asked for again. Memory drops them as soon as it sees a new
version; files let them run out. `[web] page_cache_minutes` (10) bounds any entry's life, in case a page shows
something that changes with the clock alone; the expiry is stored in the entry, so a copy between tiers keeps it.

**Backends** (`[web] page_cache`): `memory` keeps the pages in the worker's own memory (Django's local-memory cache),
`files` writes them to `<data dir>/cache/pages` where all workers share them (Django's file cache), `tiered` uses both:
the worker's memory first, then the shared files (a hit there is copied into memory), and a render goes into both.
Because a key never changes meaning, the tiers can never disagree. `auto`: memory with one worker, files with more.
`off`: nothing is cached. Under DEBUG it is always off (templates reload there). Bodies are stored zlib-compressed. A
broken or unreadable entry is a miss, never an error.

**Warming** (`[web] page_cache_warm`): a thread in each worker looks at the data version every `POLL_S` seconds. Once a
new version has held for `SETTLE_S` (a long `reprocess` bumps it every mission: nothing is warmed until it is done),
or the last warm-up is older than the entry lifetime, it renders the default variant of every cached page through the
WSGI application itself (its requests always render: they refresh entries that are about to run out), so the cache
is filled the same way a visitor's miss fills it. Variants: the site's public host (`[https] domain`) in the default
language, plus the host and language of the last visitors (`VARIANTS`). With shared files (`files`, `tiered`) one
worker per start claims each warm-up and the others skip it; under `tiered` they copy the pages into their memory on
the first request for each."""

import hashlib
import html
import io
import logging
import os
import re
import sys
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import cast
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import caches
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import get_language

from il2ks.serving.bootid import current_boot_id

log = logging.getLogger("il2ks.web.page_cache")

MEMORY_ALIAS = "pages_memory"  # settings.CACHES
FILES_ALIAS = "pages_files"
_TIERS: dict[str, tuple[str, ...]] = {
    "memory": (MEMORY_ALIAS,),
    "files": (FILES_ALIAS,),
    "tiered": (MEMORY_ALIAS, FILES_ALIAS),
}
CACHED_VIEWS = frozenset(
    {
        "web:home",
        "web:mission-list",
        "web:player-search",
        "web:sortie-list",
        "web:aircraft-list",
        "web:aircraft-detail",
        "web:leaderboards",
        "web:leaderboard",
    }
)
SKIP_PARAMS = ("q",)  # name searches: endless variants, each asked for once
STATUS_HEADER = "X-Il2ks-Cache"  # hit / miss: shows in a browser's network tab and in load tests
WARM_ENVIRON = "il2ks.page_cache.warm"  # marks the warmer's own requests (not counted as visitors)
POLL_S = 2.0
SETTLE_S = 5.0
VARIANTS = 4  # (host, language) pairs of recent visitors that are warmed besides the default one
_FORMAT = 2
# Set again on a hit by `web.caching._decorate` and by the outer middleware for this very request
_NOT_STORED = frozenset({"etag", "cache-control", "vary", "set-cookie", "content-length", STATUS_HEADER.lower()})
_AIRCRAFT_LINK = re.compile(r'href="(/aircraft/\d+/(?:\?[^"]*)?)"')  # with the list's ?tour=, as visitors click it

type Stored = tuple[int, float, bytes, tuple[tuple[str, str], ...]]  # (format, expires (epoch s), zlib body, headers)
type WsgiApp = Callable[[dict[str, object], Callable[..., object]], Iterable[bytes]]

_BOOT_ID = current_boot_id()  # the same in all workers of one `il2ks web` start (`serving.bootid`)
_lock = threading.Lock()
_memory_version: int | None = None
_visitors: OrderedDict[tuple[str, str], None] = OrderedDict()


def backend() -> str:
    """ "memory", "files", "tiered" or "off" (settings resolve "auto" and DEBUG)."""
    return str(getattr(settings, "IL2KS_PAGE_CACHE", "off"))


def _tiers() -> tuple[str, ...]:
    """The cache aliases to use, nearest first."""
    return _TIERS.get(backend(), ())


def _shared() -> bool:
    return FILES_ALIAS in _tiers()


def timeout_s() -> int:
    return int(getattr(settings, "IL2KS_PAGE_CACHE_SECONDS", 600))


def cacheable(request: HttpRequest) -> bool:
    """A public request whose page is cached: see the module docstring."""
    match = request.resolver_match
    return (
        backend() != "off"
        and request.method in {"GET", "HEAD"}
        and match is not None
        and match.view_name in CACHED_VIEWS
        and settings.SESSION_COOKIE_NAME not in request.COOKIES
        and not any(param in request.GET for param in SKIP_PARAMS)
    )


def _key(request: HttpRequest, etag: str) -> str:
    return "page:" + hashlib.sha256(f"{request.get_host()}\x1f{etag}".encode()).hexdigest()[:40]


def note_version(version: int) -> None:
    """The worker's memory forgets the pages of older versions at once (they can never be asked for again)."""
    global _memory_version
    if MEMORY_ALIAS not in _tiers() or version == _memory_version:
        return
    with _lock:
        if version != _memory_version:
            caches[MEMORY_ALIAS].clear()
            _memory_version = version


def note_visitor(request: HttpRequest) -> None:
    """Remember the host and language of a visitor, for the warmer."""
    if request.META.get(WARM_ENVIRON):
        return
    variant = (request.get_host(), get_language() or settings.LANGUAGE_CODE)
    with _lock:
        _visitors[variant] = None
        _visitors.move_to_end(variant)
        while len(_visitors) > VARIANTS:
            _visitors.popitem(last=False)


def _read(alias: str, key: str, now: float) -> Stored | None:
    try:
        stored = caches[alias].get(key)
    except Exception:  # a half-written or foreign file: rebuild the page
        log.warning("page cache: unreadable entry in %s", alias, exc_info=True)
        return None
    if not isinstance(stored, tuple) or len(cast(tuple[object, ...], stored)) != 4:
        return None
    entry = cast(Stored, stored)
    return entry if entry[0] == _FORMAT and entry[1] > now else None


def _write(alias: str, key: str, entry: Stored, now: float) -> None:
    try:
        caches[alias].set(key, entry, timeout=max(1, int(entry[1] - now)))
    except Exception:  # a full disk or a locked file must not fail the page
        log.warning("page cache: could not store in %s", alias, exc_info=True)


def lookup(request: HttpRequest, etag: str) -> HttpResponse | None:
    """The kept page, from the nearest tier that has it (copied into the nearer ones); None = render it. The warmer's
    own requests always render."""
    if request.META.get(WARM_ENVIRON):
        return None
    key = _key(request, etag)
    now = time.time()
    tiers = _tiers()
    for depth, alias in enumerate(tiers):
        entry = _read(alias, key, now)
        if entry is None:
            continue
        try:
            content = zlib.decompress(entry[2])
        except zlib.error:
            return None
        for nearer in tiers[:depth]:
            _write(nearer, key, entry, now)
        response = HttpResponse(content)
        for name, value in entry[3]:
            response[name] = value
        response[STATUS_HEADER] = "hit"
        return response
    return None


def store(request: HttpRequest, etag: str, response: HttpResponse) -> None:
    """Keep a freshly rendered 200 in every tier (the caller checked that it is cacheable)."""
    response[STATUS_HEADER] = "miss"
    if request.method != "GET" or response.streaming:
        return
    now = time.time()
    headers = tuple((k, v) for k, v in response.items() if k.lower() not in _NOT_STORED)
    entry: Stored = (_FORMAT, now + timeout_s(), zlib.compress(response.content, 1), headers)
    key = _key(request, etag)
    for alias in _tiers():
        _write(alias, key, entry, now)


# --- Warming -------------------------------------------------------------------------------------------------------


def default_variant() -> tuple[str, str]:
    """The public host (`[https] domain`, else localhost) in the site's default language."""
    public = urlsplit(str(getattr(settings, "IL2KS_PUBLIC_URL", "")))
    return (public.netloc or "localhost", settings.LANGUAGE_CODE)


def variants() -> list[tuple[str, str]]:
    with _lock:
        recent = list(reversed(_visitors))
    found = [default_variant(), *recent]
    return list(dict.fromkeys(found))


def page_paths() -> list[str]:
    """The default variant of every cached page, the most visited first. Aircraft pages come from the aircraft list."""
    from il2ks.queries import leaderboards as boards  # Django models: not at import time of the settings

    paths = [reverse(f"web:{name}") for name in ("home", "mission-list", "leaderboards", "player-search")]
    paths += [reverse("web:sortie-list"), reverse("web:aircraft-list")]
    paths += [reverse("web:leaderboard", args=[key]) for key in boards.BOARDS]
    return paths


def render(app: WsgiApp, url: str, host: str, language: str) -> tuple[str, bytes]:
    """One GET of a local URL (path and query) through the WSGI application, as a visitor behind the HTTPS proxy would
    send it."""
    path, _, query = url.partition("?")
    environ: dict[str, object] = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": host.rsplit(":", 1)[0].strip("[]"),
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": host,
        "HTTP_ACCEPT_LANGUAGE": language,
        "HTTP_X_FORWARDED_PROTO": "https",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
        WARM_ENVIRON: True,
    }
    status: list[str] = []

    def start_response(code: str, _headers: object, _exc: object = None) -> None:
        status.append(code)

    result = app(environ, start_response)
    try:
        body = b"".join(result)
    finally:
        close = getattr(result, "close", None)
        if callable(close):
            close()  # request_finished: connections are handled as after any request
    return (status[0] if status else "", body)


def warm(app: WsgiApp) -> tuple[int, float]:
    """Render every cached page in every variant. Returns (pages, seconds)."""
    start = time.perf_counter()
    count = 0
    base = page_paths()
    aircraft_list = reverse("web:aircraft-list")
    for host, language in variants():
        paths = list(base)
        for path in base:
            code, body = render(app, path, host, language)
            count += 1
            if path == aircraft_list and code.startswith("200"):
                links = _AIRCRAFT_LINK.findall(body.decode("utf-8", "replace"))
                paths += list(dict.fromkeys(html.unescape(link) for link in links))
        for path in paths[len(base) :]:
            render(app, path, host, language)
            count += 1
    return count, time.perf_counter() - start


def _current_version() -> int:
    from il2ks.db.models import DataVersion

    row = DataVersion.objects.filter(pk=1).only("version").first()
    return row.version if row is not None else 0


def _claim(version: int, round_no: int) -> bool:
    """Shared files: the first worker of this start to claim a warm-up does it. Memory: every worker warms its own.

    The start ID is in the claim: a process left over from an earlier start (its pages are keyed on its own ID, which no
    visitor asks for) must not take the warm-up from the running ones."""
    if not _shared():
        return True
    try:
        claim = f"warm:{_BOOT_ID}:{version}:{round_no}"
        return bool(caches[FILES_ALIAS].add(claim, os.getpid(), timeout=timeout_s()))
    except Exception:
        return True


class Warmer(threading.Thread):
    def __init__(self, app: WsgiApp, *, poll_s: float = POLL_S, settle_s: float = SETTLE_S) -> None:
        super().__init__(name="il2ks-page-cache-warmer", daemon=True)
        self.app = app
        self.poll_s = poll_s
        self.settle_s = settle_s
        self.stop = threading.Event()

    def run(self) -> None:
        seen: int | None = None
        seen_at = 0.0
        warmed: tuple[int, float] | None = None  # (version, when)
        while not self.stop.wait(self.poll_s):
            try:
                close_old_connections()
                version = _current_version()
            except Exception:
                log.warning("page cache warmer: could not read the data version", exc_info=True)
                continue
            now = time.monotonic()
            if version != seen:
                seen, seen_at = version, now
                continue
            if now - seen_at < self.settle_s:
                continue
            if warmed is not None and warmed[0] == version and now - warmed[1] < timeout_s() * 0.9:
                continue
            round_no = int(time.time() // max(timeout_s() * 0.9, 1))
            if _claim(version, round_no):
                try:
                    pages, seconds = warm(self.app)
                except Exception:
                    log.warning("page cache warmer: a page failed", exc_info=True)
                else:
                    log.info("page cache: warmed %d pages in %.2f s (data version %d)", pages, seconds, version)
            warmed = (version, now)


def start_warmer(app: WsgiApp) -> Warmer | None:
    """Called once per web worker (`il2ks.asgi`)."""
    if backend() == "off" or not getattr(settings, "IL2KS_PAGE_CACHE_WARM", False):
        return None
    warmer = Warmer(app)
    warmer.start()
    return warmer
