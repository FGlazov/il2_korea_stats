"""Throttle for failed admin logins (NFR-SEC, FR-ADM-1): a few wrong passwords from one address lock that address out.

Only `POST /admin/login/` is watched. A failed attempt is a 200 response (Django re-renders the form with the error), a
successful one is a redirect. After `MAX_FAILURES` failures within `WINDOW_SECONDS` further attempts from the same
address get `429` without the password being checked, until the window ends. A successful login clears the count.

The count lives in Django's cache (per process; with `[web] workers > 1` each worker counts on its own, so the real
limit is at most `workers` times higher). The client address is the last `X-Forwarded-For` entry, which is what the
reverse proxy in front (Caddy, nginx) appended, falling back to the socket's address: behind a proxy that is the only
value the client cannot choose.
"""

from collections.abc import Callable

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext as _

MAX_FAILURES = 10
WINDOW_SECONDS = 300


def client_address(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    last = forwarded.rsplit(",", 1)[-1].strip()
    return last or request.META.get("REMOTE_ADDR", "")


def _key(request: HttpRequest) -> str:
    return f"il2ks:login-failures:{client_address(request)}"


def _is_login_attempt(request: HttpRequest) -> bool:
    return request.method == "POST" and request.path == reverse("admin:login")


class LoginThrottleMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not _is_login_attempt(request):
            return self.get_response(request)
        key = _key(request)
        if cache.get(key, 0) >= MAX_FAILURES:
            response = HttpResponse(
                _("Too many failed login attempts. Try again in a few minutes."),
                status=429,
                content_type="text/plain; charset=utf-8",
            )
            response["Retry-After"] = str(WINDOW_SECONDS)
            return response
        response = self.get_response(request)
        if response.status_code == 200:  # the form again: wrong credentials
            cache.add(key, 0, WINDOW_SECONDS)
            try:
                cache.incr(key)
            except ValueError:  # the entry expired between add and incr
                cache.set(key, 1, WINDOW_SECONDS)
        elif response.status_code in {301, 302, 303}:
            cache.delete(key)
        return response
