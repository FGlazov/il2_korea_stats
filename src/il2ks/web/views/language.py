"""The language switcher (TD-24): `GET /language/?language=de&next=/players/` stores the choice in a cookie, redirects.

Not Django's `set_language` view: that one needs a POST with a CSRF token, which would put a CSRF cookie on every public
page and take them out of the shared cache (`web.caching` skips responses that set a cookie). A GET that only stores a
harmless display preference needs no token. The redirect is never cached; the pages `Vary` on the cookie.
"""

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def set_language(request: HttpRequest) -> HttpResponse:
    """Remember `?language=<code>` (one of `settings.LANGUAGES`) and go back to `?next=` (a local URL, else home)."""
    target = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = "/"
    response = HttpResponseRedirect(target)
    code = request.GET.get("language", "")
    if code in {known for known, _name in settings.LANGUAGES}:
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            code,
            max_age=settings.LANGUAGE_COOKIE_AGE,
            path=settings.LANGUAGE_COOKIE_PATH,
            domain=settings.LANGUAGE_COOKIE_DOMAIN,
            secure=settings.LANGUAGE_COOKIE_SECURE,
            httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
            samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        )
    return response
