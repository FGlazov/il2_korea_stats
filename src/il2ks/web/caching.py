"""HTTP revalidation keyed on the data version (TD-28).

Page data only changes when a mission is saved, aggregates are rebuilt or an admin edits something the pages show; each
of those bumps `DataVersion` (`il2ks.db.site.bump_data_version`). So for public GET/HEAD pages the middleware:

- builds a strong `ETag` from (data version, language, il2ks version, full path with query, `HX-Request`),
- answers a matching `If-None-Match` with `304` **before the view runs** (`process_view`): the whole cost of a
  revalidation is one tiny query for the version,
- adds that ETag, `Cache-Control: public, max-age=60` and `Vary: HX-Request, Accept-Language` to 200 responses.

Left alone: non-GET/HEAD, `/admin/`, media and static URLs, responses that set a cookie, that aren't 200, or that
already carry their own `Cache-Control` (live fragments, FR-ING-12/15, manage their own freshness; they never get an
ETag from us, so a client never revalidates them against the data version).
"""

import hashlib
from collections.abc import Callable
from typing import cast

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotModified
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.translation import get_language

from il2ks import __version__
from il2ks.db.site import current_data_version

MAX_AGE = 60
VARY = ("HX-Request", "Accept-Language")
_ETAG_ATTR = "_il2ks_etag"

type View = Callable[..., HttpResponse]


def make_etag(request: HttpRequest, version: int) -> str:
    """Strong, quoted ETag for this request at this data version."""
    parts = (
        str(version),
        get_language() or "",
        __version__,
        request.get_full_path(),
        request.headers.get("HX-Request", ""),
    )
    return '"' + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32] + '"'


def _excluded_prefixes() -> tuple[str, ...]:
    static = "/" + settings.STATIC_URL.strip("/") + "/"
    return ("/admin/", "/" + settings.MEDIA_URL.strip("/") + "/", static)


def _matches(if_none_match: str, etag: str) -> bool:
    """Weak comparison (RFC 9110 for `If-None-Match`): `W/` prefixes are ignored; `*` matches anything."""
    tags = [t.strip().removeprefix("W/") for t in if_none_match.split(",")]
    return "*" in tags or etag in tags


def _decorate(response: HttpResponse, etag: str) -> None:
    response["ETag"] = etag
    patch_cache_control(response, public=True, max_age=MAX_AGE)
    patch_vary_headers(response, VARY)  # pyright: ignore[reportArgumentType]


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
            _decorate(response, etag)
        return response

    def process_view(
        self, request: HttpRequest, view_func: View, view_args: tuple[object, ...], view_kwargs: dict[str, object]
    ) -> HttpResponse | None:
        if request.method not in {"GET", "HEAD"} or request.path.startswith(_excluded_prefixes()):
            return None
        etag = make_etag(request, current_data_version())
        setattr(request, _ETAG_ATTR, etag)
        if _matches(request.headers.get("If-None-Match", ""), etag):
            response = HttpResponseNotModified()
            _decorate(response, etag)
            return response
        return None
