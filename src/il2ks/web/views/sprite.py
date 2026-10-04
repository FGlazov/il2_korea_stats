"""The icon sprite (`web.icons`): every icon file as a <symbol>, one site-wide file that pages reference with <use>.

Not a static file: it is built from the static finders so a `custom/static/il2ks/img/...` override (TD-25) is in it. The
URL carries the sprite's content hash (`?v=`), so a response for the current hash is cached for a year; a stale or
missing hash gets the current sprite with revalidation only. Because it sets its own `Cache-Control`, `web.caching`
leaves it alone.
"""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe

from il2ks.web import icons


@require_safe
def icon_sprite(request: HttpRequest) -> HttpResponse:
    text, digest = icons.sprite()
    response = HttpResponse(text, content_type="image/svg+xml")
    if request.GET.get("v") == digest:
        response["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response["Cache-Control"] = "no-cache"
    return response
