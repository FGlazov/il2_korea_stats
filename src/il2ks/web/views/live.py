"""`GET /live/`: the online-now fragment that HTMX polls (FR-ING-12, FR-WEB-15, TD-28 "live data").

Live data changes every few seconds, so it sits outside the data-version scheme: the response carries its own
`Cache-Control: public, max-age=15`, which makes `DataVersionCacheMiddleware` leave it alone (no ETag, no 304 from the
data version). Public, because hidden players are already filtered out of what it shows.

While `watch` is committing a snapshot SQLite can be busy for a moment. The fragment then answers `503` with
`Retry-After` and `no-store`: htmx doesn't swap an error response, so the visitor keeps what the section showed and the
next poll tries again.
"""

from django.db import OperationalError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers, patch_cache_control, patch_vary_headers

from il2ks.queries import live

MAX_AGE = 15
RETRY_AFTER_S = 5


def live_fragment(request: HttpRequest) -> HttpResponse:
    try:
        view = live.current()
    except OperationalError:
        busy = HttpResponse(status=503)
        busy["Retry-After"] = str(RETRY_AFTER_S)
        add_never_cache_headers(busy)
        return busy
    response = render(request, "il2ks/components/online_now_body.html", {"live": view})
    patch_cache_control(response, public=True, max_age=MAX_AGE)
    patch_vary_headers(response, ("Accept-Language", "Cookie"))  # pyright: ignore[reportArgumentType]
    return response
