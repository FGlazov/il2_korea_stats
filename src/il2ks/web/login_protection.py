"""Admin login protection (NFR-SEC-8): django-axes with its database handler does the counting and the locking.

Settings (`il2ks.settings`): after `[web] login_attempts` wrong passwords (default 5) for one account from one
address, that **pair** is locked for `[web] login_lockout_minutes` (default 15): the same account is still open from
every other address, so a stranger cannot lock the real admin out. A looser lock covers one address on its own: after
`IL2KS_LOGIN_ADDRESS_FACTOR` (3) times that many wrong passwords from it, for any accounts, the address is locked
(somebody trying many account names). The lock is a row in axes's `AccessAttempt` table, so it survives restarts and
needs no cache service; `il2ks admin unlock` (or deleting the row in the admin, "Access attempts") unlocks.
A correct login clears the count. This module adds what axes does not do for us:

- `LoginHandler`: axes's database handler with the two different limits (axes has only one).
- `client_address`: who the client is, as the lockout sees it: `raw_client_address` below, with an IPv6 address cut to
  its /64 (one host or home network owns 2^64 addresses, so the full address would give an attacker endless fresh
  counters; the bucket is stored as the network address, `2001:db8:1:2::`, shown as `2001:db8:1:2::/64`), and an
  IPv4-mapped IPv6 address (`::ffff:1.2.3.4`) as the IPv4. `raw_client_address` is the socket's address, except when
  the connection comes from this machine and
  the site runs in production (the bundled Caddy, or the admin's own proxy on the same machine; the web server
  listens on 127.0.0.1 there, docs/reverse-proxy.md): then the last `X-Forwarded-For` entry, which is the one that
  proxy appended and the client cannot choose. From any other socket address, or in development, the header is
  client-controlled and ignored.
- `lockout_response`: the plain "try again in N minutes" page, in the admin's look and the visitor's language.
- structured log lines for failed attempts and lockouts (TD-27): the JSON log carries `event`, `username`,
  `ip_address` (the bucket) and
  `client_address` (the full address).
- `current_lockouts`: what `il2ks doctor` lists.
"""

import ipaddress
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from axes.handlers.database import AxesDatabaseHandler  # pyright: ignore[reportMissingTypeStubs]
from axes.models import AccessAttempt  # pyright: ignore[reportMissingTypeStubs]
from axes.signals import user_locked_out  # pyright: ignore[reportMissingTypeStubs]
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.signals import user_login_failed
from django.db.models import QuerySet, Sum
from django.dispatch import receiver
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

log = logging.getLogger(__name__)

LOCKED_TEMPLATE = "admin/il2ks_login_locked.html"
MAX_LOGGED_NAME = 150


ADDRESS_FACTOR_DEFAULT = 3
IPV6_BUCKET_BITS = 64


def address_factor() -> int:
    return int(getattr(settings, "IL2KS_LOGIN_ADDRESS_FACTOR", ADDRESS_FACTOR_DEFAULT))


class LoginHandler(AxesDatabaseHandler):
    """axes counts each lockout parameter group (`AXES_LOCKOUT_PARAMETERS`: the pair, then the address) and locks at
    one shared limit. Here the address group has to reach `address_factor()` times the limit: its count is scaled
    down, so the shared comparison `failures >= AXES_FAILURE_LIMIT` means the right thing for both."""

    def get_failures(self, request: HttpRequest, credentials: dict[str, Any] | None = None) -> int:
        groups = cast("list[QuerySet[AccessAttempt]]", self.get_user_attempts(request, credentials))  # pyright: ignore[reportUnknownMemberType]
        pair, address = (int(group.aggregate(total=Sum("failures_since_start"))["total"] or 0) for group in groups)
        return max(pair, address // address_factor())


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.strip("[]")).is_loopback
    except ValueError:
        return False


def _without_port(entry: str) -> str:
    """IIS ARR appends the client port to X-Forwarded-For: `1.2.3.4:51234` and `[2001:db8::1]:51234`."""
    if entry.startswith("["):
        host, _, rest = entry[1:].partition("]")
    elif entry.count(":") == 1:  # an IPv4 address with a port; a bare IPv6 address has several colons
        host, _, rest = entry.partition(":")
        rest = ":" + rest
    else:
        return entry
    return host if rest == "" or (rest.startswith(":") and rest[1:].isdigit()) else entry


def bucket(address: str) -> str:
    """The lockout key of an address: IPv4 as is, IPv4-mapped IPv6 as the IPv4, other IPv6 as its /64 network address.
    Not an IP address (empty, junk): returned unchanged."""
    try:
        parsed = ipaddress.ip_address(address.strip().strip("[]"))
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.IPv6Network((parsed, IPV6_BUCKET_BITS), strict=False).network_address)
    return str(parsed)


def bucket_label(name: str) -> str:
    """How a bucket is shown to people: IPv6 buckets get their `/64`."""
    return f"{name}/{IPV6_BUCKET_BITS}" if ":" in name else name


def normalise_unlock_address(text: str) -> str:
    """`il2ks admin unlock --ip`: any spelling of an address (case, compressed forms, `/64`) to the stored bucket."""
    try:
        return bucket(str(ipaddress.ip_interface(text.strip().strip("[]")).ip))
    except ValueError:
        raise ValueError(f"{text!r} is not an IP address") from None


def client_address(request: HttpRequest) -> str:
    """The lockout key of the client (see `bucket`); django-axes stores this as the attempt's `ip_address`."""
    return bucket(raw_client_address(request))


def raw_client_address(request: HttpRequest) -> str:
    remote = request.META.get("REMOTE_ADDR", "")
    if not getattr(settings, "IL2KS_TRUST_FORWARDED_FOR", False) or not _is_loopback(remote):
        return remote
    last = _without_port(request.META.get("HTTP_X_FORWARDED_FOR", "").rsplit(",", 1)[-1].strip())
    try:
        return str(ipaddress.ip_address(last)) if last else remote
    except ValueError:
        return remote  # junk from a broken proxy: one shared bucket, never a client-chosen one


def minutes_left(until: datetime, now: datetime) -> int:
    """Whole minutes to show for a lock that ends at `until`: rounded up, never below 1."""
    return max(1, math.ceil((until - now).total_seconds() / 60))


def _cool_off() -> timedelta:
    value = settings.AXES_COOLOFF_TIME
    assert isinstance(value, timedelta)
    return value


def _minutes_until_unlocked(address: str, username: str | None, now: datetime) -> int:
    """Minutes until every lock that applies to this request is over. The pair lock ends when its (newest) row
    expires; the address lock when the rows still inside the window add up to less than the address limit again, which
    is not the newest attempt. No lock found (axes decided otherwise): the full lock time."""
    window = _cool_off()
    limit = settings.AXES_FAILURE_LIMIT
    rows = list(
        AccessAttempt.objects.filter(ip_address=address, attempt_time__gt=now - window).order_by("attempt_time")
    )
    ends: list[datetime] = []
    pair = [row for row in rows if username and row.username == username]
    if sum(row.failures_since_start for row in pair) >= limit:
        ends.append(max(row.attempt_time for row in pair) + window)
    total = sum(row.failures_since_start for row in rows)
    needed = limit * address_factor()
    if total >= needed:
        for row in rows:  # the oldest rows expire first
            total -= row.failures_since_start
            if total < needed:
                ends.append(row.attempt_time + window)
                break
    return minutes_left(max(ends), now) if ends else math.ceil(window.total_seconds() / 60)


def lockout_response(
    request: HttpRequest,
    response: HttpResponse | None = None,
    credentials: dict[str, Any] | None = None,
) -> HttpResponse:
    """axes calls this when the request is locked out. A plain page, status 429 (`Retry-After` in seconds)."""
    now = timezone.now()
    name = (credentials or {}).get("username")
    left = _minutes_until_unlocked(client_address(request), name, now)
    context = {**admin.site.each_context(request), "title": "", "minutes": left}
    page = render(request, LOCKED_TEMPLATE, context, status=429)
    page["Retry-After"] = str(left * 60)
    return page


# --- structured log (TD-27) ----------------------------------------------------------------------


@receiver(user_login_failed)
def log_failed_login(
    sender: object, credentials: dict[str, object], request: HttpRequest | None = None, **kwargs: object
) -> None:
    """`ip_address` is the lockout bucket (what `il2ks admin unlock --ip` and doctor use), `client_address` the full
    address, so a log reader can still tell hosts of one /64 apart."""
    name = str(credentials.get("username", ""))[:MAX_LOGGED_NAME]
    full = raw_client_address(request) if request is not None else ""
    log.warning(
        "failed admin login for %r from %s",
        name,
        full,
        extra={"event": "admin_login_failed", "username": name, "ip_address": bucket(full), "client_address": full},
    )
    if name:
        _warn_if_account_is_targeted(name)


def _warn_if_account_is_targeted(name: str) -> None:
    """One account failing at many addresses never locks it (a stranger could lock the real admin out), but when the
    failures across all addresses reach the address limit it is logged: somebody is guessing from many addresses."""
    since = timezone.now() - _cool_off()
    rows = AccessAttempt.objects.filter(username=name, attempt_time__gt=since)
    total = int(rows.aggregate(total=Sum("failures_since_start"))["total"] or 0)
    addresses = rows.count()
    if total >= settings.AXES_FAILURE_LIMIT * address_factor() and addresses > 1:
        log.warning(
            "admin account %r had %d wrong passwords from %d addresses within the lock time: it may be under attack",
            name,
            total,
            addresses,
            extra={"event": "admin_login_account_targeted", "username": name, "failures": total},
        )


@receiver(user_locked_out)
def log_lockout(
    sender: object, request: HttpRequest, username: str | None, ip_address: str | None, **kwargs: object
) -> None:
    name = (username or "")[:MAX_LOGGED_NAME]
    full = raw_client_address(request)
    log.warning(
        "admin login locked out for %r from %s (limit %d wrong passwords, lock %d minutes)",
        name,
        full,
        settings.AXES_FAILURE_LIMIT,
        int(_cool_off().total_seconds() // 60),
        extra={"event": "admin_login_locked", "username": name, "ip_address": ip_address, "client_address": full},
    )


# --- `il2ks doctor` ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lock:
    kind: str  # "account" (one account at one address) or "address" (one address, any accounts)
    name: str
    minutes_left: int


def current_lockouts(now: datetime | None = None) -> list[Lock]:
    """What is locked right now: failures inside the lock window reach the limit (see `LoginHandler`)."""
    now = now or timezone.now()
    window = _cool_off()
    limit = settings.AXES_FAILURE_LIMIT
    failures: dict[tuple[str, str], int] = defaultdict(int)
    newest: dict[tuple[str, str], datetime] = {}
    for attempt in AccessAttempt.objects.filter(attempt_time__gt=now - window):
        shown = bucket_label(attempt.ip_address or "")
        keys = [("address", shown)]
        if attempt.username:
            keys.append(("account", f"{attempt.username} from {shown}"))
        for key in keys:
            failures[key] += attempt.failures_since_start
            newest[key] = max(newest.get(key, attempt.attempt_time), attempt.attempt_time)
    locks = [
        Lock(kind, name, minutes_left(newest[kind, name] + window, now))
        for (kind, name), count in failures.items()
        if count >= (limit if kind == "account" else limit * address_factor())
    ]
    return sorted(locks, key=lambda lock: (lock.kind, lock.name))


def unlock(*, username: str | None = None, ip: str | None = None) -> int:
    """Delete the failure counts that match (both, when both are given; everything when neither): the locks end.
    `ip` is normalised like the lockout key (case, compressed forms, `/64`, IPv4-mapped); not an address: ValueError.
    Returns the number of rows removed."""
    attempts = AccessAttempt.objects.all()
    if username:
        attempts = attempts.filter(username=username)
    if ip:
        attempts = attempts.filter(ip_address=normalise_unlock_address(ip))
    return attempts.delete()[0]
