"""`GET /live/`: the online-now fragment that HTMX polls (FR-ING-12, FR-WEB-15, TD-28 "live data").

Live data changes every few seconds, so it sits outside the data-version scheme: the response carries its own
`Cache-Control: public, max-age=15`, which makes `DataVersionCacheMiddleware` leave it alone (no ETag, no 304 from the
data version). Public, because hidden players are already filtered out of what it shows.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.cache import patch_cache_control, patch_vary_headers

from il2ks.queries import live

MAX_AGE = 15


def live_fragment(request: HttpRequest) -> HttpResponse:
    response = render(request, "il2ks/components/online_now_body.html", {"live": live.current()})
    patch_cache_control(response, public=True, max_age=MAX_AGE)
    patch_vary_headers(response, ("Accept-Language",))
    return response
