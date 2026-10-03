"""Django settings derived from the loaded `Config` (TD-11, NFR-SEC-2, TD-23).

`il2ks/settings.py` is a thin file that loads the config and assigns what these functions return. Keeping the rules
here, as pure functions of a `Config`, lets tests check every mode (development, production with a domain, with an IP,
behind an external proxy) without importing Django's settings machinery.
"""

import ipaddress
from dataclasses import dataclass

from il2ks.config import Config

INSTALLED_APPS = [
    "il2ks.web",  # first, so its `admin/base_site.html` (the override warning banner, TD-25) wins over Django's
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "il2ks.db",
]
"""Order matters twice: Django looks for templates and static files in this order, and `il2ks custom` follows it."""

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
"""Django believes this header from the proxy (Caddy sets it itself; nginx and IIS must, see docs/reverse-proxy.md)."""
STATIC_BACKEND_DEV = "django.contrib.staticfiles.storage.StaticFilesStorage"
STATIC_BACKEND_PROD = "il2ks.serving.storage.LenientManifestStorage"


def _is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host.strip("[]")), ipaddress.IPv6Address)
    except ValueError:
        return False


def host_for_url(host: str) -> str:
    """`host` as it appears in a URL or `ALLOWED_HOSTS`: IPv6 addresses in brackets."""
    return f"[{host.strip('[]')}]" if _is_ipv6(host) else host


def allowed_hosts(cfg: Config) -> list[str]:
    """Host names Django accepts: `[https] domain`, `[web] allowed_hosts`, localhost.

    No domain and no explicit list in production: the proxy is the only way in and the admin has not told us the name
    (IP-address and test setups), so every Host is accepted, and `il2ks doctor` nudges to set a domain."""
    hosts: list[str] = []
    if cfg.https.domain:
        hosts.append(host_for_url(cfg.https.domain))
    hosts.extend(cfg.web.allowed_hosts)
    hosts.extend(LOCAL_HOSTS)
    if not cfg.debug and not cfg.https.domain and not cfg.web.allowed_hosts:
        hosts.append("*")
    return list(dict.fromkeys(hosts))


def csrf_trusted_origins(cfg: Config) -> list[str]:
    """`https://` origins for the domain (with the port when it is not 443) and the extra allowed hosts."""
    origins: list[str] = []
    if cfg.https.domain:
        host = host_for_url(cfg.https.domain)
        origins.append(f"https://{host}")
        if cfg.https.https_port != 443:
            origins.append(f"https://{host}:{cfg.https.https_port}")
    for host in cfg.web.allowed_hosts:
        if host == "*":
            continue
        origins.append("https://*" + host if host.startswith(".") else f"https://{host_for_url(host)}")
    return list(dict.fromkeys(origins))


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    proxy_ssl_header: tuple[str, str] | None
    ssl_redirect: bool
    hsts_seconds: int
    hsts_include_subdomains: bool
    hsts_preload: bool
    session_cookie_secure: bool
    csrf_cookie_secure: bool
    session_cookie_httponly: bool
    csrf_cookie_httponly: bool
    content_type_nosniff: bool
    referrer_policy: str


def security_settings(cfg: Config) -> SecuritySettings:
    """HTTPS-only behaviour in production (TD-23); all HTTPS parts off in debug so plain-http localhost works.

    HSTS starts modest (`[https] hsts_seconds`, one day), never includes sub-domains and never asks for preload: a
    mistake there can't be undone from the server side. The headers that don't need HTTPS are on in both modes."""
    https = not cfg.debug
    return SecuritySettings(
        proxy_ssl_header=PROXY_SSL_HEADER if https else None,
        ssl_redirect=https,
        hsts_seconds=cfg.https.hsts_seconds if https else 0,
        hsts_include_subdomains=False,
        hsts_preload=False,
        session_cookie_secure=https,
        csrf_cookie_secure=https,
        session_cookie_httponly=True,
        csrf_cookie_httponly=True,
        content_type_nosniff=True,
        referrer_policy="same-origin",
    )


def staticfiles_backend(cfg: Config) -> str:
    """Hashed, compressed file names in production (TD-28); plain files in development (no `collectstatic` needed)."""
    return STATIC_BACKEND_DEV if cfg.debug else STATIC_BACKEND_PROD
