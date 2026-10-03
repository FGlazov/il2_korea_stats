"""Throttle for failed admin logins (NFR-SEC, FR-ADM-1): a few wrong passwords from one address lock that address out.

Only `POST /admin/login/` is watched. A failed attempt is a 200 response (Django re-renders the form with the error), a
successful one is a redirect. After `MAX_FAILURES` failures within `WINDOW_SECONDS` further attempts from the same
address get `429` without the password being checked, until the window ends. A successful login clears the count.

The count lives in Django's cache (per process; with `[web] workers > 1` each worker counts on its own, so the real
limit is at most `workers` times higher). The client address is the socket's address, except when that is a loopback
address (the bundled Caddy or a proxy on this machine; the web server only listens on 127.0.0.1 in that setup): then it
is the last `X-Forwarded-For` entry, which is what that proxy appended and the client cannot choose. From any other
socket address the header is client-controlled and ignored, so a client reaching the web server directly can't pick
its own bucket. A proxy that sends no `X-Forwarded-For` puts everyone in one bucket (docs/reverse-proxy.md).
"""

import ipaddress
from collections.abc import Callable

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext as _

MAX_FAILURES = 10
WINDOW_SECONDS = 300


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.strip("[]")).is_loopback
    except ValueError:
        return False


def client_address(request: HttpRequest) -> str:
    remote = request.META.get("REMOTE_ADDR", "")
    if not _is_loopback(remote):
        return remote
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    last = forwarded.rsplit(",", 1)[-1].strip()
    return last or remote


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
