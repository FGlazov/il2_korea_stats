"""HTTP revalidation keyed on the data version (TD-28).

Page data only changes when a mission is saved, aggregates are rebuilt or an admin edits something the pages show; each
of those bumps `DataVersion` (`il2ks.db.site.bump_data_version`). So for public GET/HEAD pages the middleware:

- builds a strong `ETag` from (data version, language, il2ks version, process start, full path with query,
  `HX-Request`),
- answers a matching `If-None-Match` with `304` **before the view runs** (`process_view`): the whole cost of a
  revalidation is one tiny query for the version,
- adds that ETag, `Cache-Control: max-age=0, must-revalidate` and `Vary: HX-Request, Accept-Language` to 200 responses
  (plus `Cookie` when the request carries the language cookie). Browsers keep the page but ask before every reuse, so
  hiding a cheater or editing the branding shows up at once (TD-28); the question costs one tiny query.

Left alone: non-GET/HEAD, `/admin/`, media and static URLs, responses that set a cookie, that aren't 200, or that
already carry their own `Cache-Control` (live fragments, FR-ING-12/15, manage their own freshness; they never get an
ETag from us, so a client never revalidates them against the data version).

Language (TD-24): the viewer's language comes from the `django_language` cookie (the switcher, `web.views.language`) or
else from `Accept-Language`. The ETag carries the active language, so a revalidation after a switch never gets a 304
for the old language. `Vary: Cookie` (sent when the language cookie is present) makes a *browser* (or proxy) cache
skip its stored copy after the cookie changes. Public visitors carry no other cookie, so this costs nothing in practice.
"""

import hashlib
from collections.abc import Callable
from typing import cast

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotModified
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.translation import get_language

from il2ks import __version__
from il2ks.db.models import DataVersion
from il2ks.serving.bootid import current_boot_id

VARY = ("HX-Request", "Accept-Language")
_ETAG_ATTR = "_il2ks_etag"
# Changes per `il2ks web` start, the same in all its workers (`serving.bootid`). Template and static overrides in
# custom/ (TD-25) only take effect after a restart (templates are cached in production), and a restart must also
# invalidate what browsers revalidate.
_BOOT_ID = current_boot_id()

type View = Callable[..., HttpResponse]

_ROW_ATTR = "_il2ks_data_version_row"
_NO_ROW = DataVersion(pk=1, version=0)


def request_data_version(request: HttpRequest) -> DataVersion | None:
    """The `DataVersion` row (version and `updated_at`), read once per request and shared by this middleware and the
    `site` context processor. None before the first data change."""
    row = getattr(request, _ROW_ATTR, None)
    if row is None:
        row = DataVersion.objects.filter(pk=1).only("version", "updated_at").first() or _NO_ROW
        setattr(request, _ROW_ATTR, row)
    return None if row is _NO_ROW else cast(DataVersion, row)


def make_etag(request: HttpRequest, version: int) -> str:
    """Strong, quoted ETag for this request at this data version."""
    parts = (
        str(version),
        get_language() or "",
        __version__,
        _BOOT_ID,
        request.get_full_path(),
        request.headers.get("HX-Request", ""),
    )
    return '"' + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32] + '"'


def _excluded_prefixes() -> tuple[str, ...]:
    static = "/" + settings.STATIC_URL.strip("/") + "/"
    return ("/admin/", "/" + settings.MEDIA_URL.strip("/") + "/", static)


def _matches(if_none_match: str, etag: str) -> bool:
    """Weak comparison (RFC 9110 for `If-None-Match`): `W/` prefixes are ignored. `*` is deliberately not special: it
    is for conditional writes, and answering it with 304 would hide a 404 the view would give."""
    return etag in [t.strip().removeprefix("W/") for t in if_none_match.split(",")]


def _decorate(request: HttpRequest, response: HttpResponse, etag: str) -> None:
    response["ETag"] = etag
    patch_cache_control(response, max_age=0, must_revalidate=True)
    patch_vary_headers(response, VARY)  # pyright: ignore[reportArgumentType]
    if settings.LANGUAGE_COOKIE_NAME in request.COOKIES:
        patch_vary_headers(response, ("Cookie",))


class DataVersionCacheMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        etag = cast("str | None", getattr(request, _ETAG_ATTR, None))
        if (
            etag is not None
            and response.status_code == 200
            and not response.cookies
            and not response.has_header("Cache-Control")
            and not response.has_header("ETag")
        ):
            _decorate(request, response, etag)
        return response

    def process_view(
        self, request: HttpRequest, view_func: View, view_args: tuple[object, ...], view_kwargs: dict[str, object]
    ) -> HttpResponse | None:
        if request.method not in {"GET", "HEAD"} or request.path.startswith(_excluded_prefixes()):
            return None
        row = request_data_version(request)
        etag = make_etag(request, row.version if row is not None else 0)
        setattr(request, _ETAG_ATTR, etag)
        if _matches(request.headers.get("If-None-Match", ""), etag):
            response = HttpResponseNotModified()
            _decorate(request, response, etag)
            return response
        return None
