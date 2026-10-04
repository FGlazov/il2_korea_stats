"""The first-run setup page's gate: a one-time token, "is this request from this very machine", and a brake on guessing.

The setup page (`il2ks.web.views.setup`, doc 07 option B) can create the admin account, so on a public server it must be
unreachable to everybody but the person who installed il2ks. Three independent locks, all of which must open:

1. **Pending.** The page exists only while `<data dir>/setup-token.txt` exists. `il2ks web` / `il2ks run`
   create that file when no admin account exists yet and print the address with the token; finishing the setup (or
   any admin account appearing) deletes it, and the page then answers 404 forever.
2. **Local.** The request must come straight from this machine: a loopback peer address, no proxy forwarding headers
   (Caddy and every other reverse proxy add them, so the public site can never reach the page), and a `localhost` Host.
3. **Token.** The URL carries the token (`/setup/?token=...`), compared in constant time. Wrong guesses are counted and
   after a few the page locks for a while (`AttemptLimiter`).

Pure Python: no Django here, so it is tested without a database.
"""

import contextlib
import ipaddress
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

TOKEN_FILE = "setup-token.txt"
FINISHING_FILE = "setup-finishing.txt"
FINISHING_MAX_AGE_S = 60.0
SETUP_PAGE_ENV = "IL2KS_SETUP_PAGE"
SETUP_PATH = "/setup/"
LOCAL_HOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
FORWARDING_HEADERS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_X_REAL_IP",
    "HTTP_FORWARDED",
    "HTTP_VIA",
    "HTTP_CLIENT_IP",
)
"""WSGI names of the headers a reverse proxy adds. Any of them present means "not a direct local request"."""

MAX_FAILURES = 5
LOCKOUT_SECONDS = 600.0


def token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILE


def read_token(data_dir: Path) -> str:
    """The pending token, or "" when setup is not pending (no file, or an empty one)."""
    try:
        return token_path(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_token(data_dir: Path) -> str:
    """The pending token, creating the file (private to its owner where the OS allows) when there is none.

    An existing token is kept, so an address printed earlier still works after a restart."""
    existing = read_token(data_dir)
    if existing:
        return existing
    token = secrets.token_urlsafe(24)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = token_path(data_dir)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{token}\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return token


def discard_token(data_dir: Path) -> None:
    """Setup is complete (or an admin exists): the page is gone for good."""
    with contextlib.suppress(OSError):
        token_path(data_dir).unlink(missing_ok=True)


def page_disabled(env: Mapping[str, str]) -> bool:
    """`IL2KS_SETUP_PAGE=off` (the Docker image sets it): no token is made and the page stays a 404. A container's
    browser never reaches the page anyway (the peer is not loopback): announcing it would mislead."""
    return env.get(SETUP_PAGE_ENV, "").strip().lower() in {"off", "0", "no", "false"}


def mark_finishing(data_dir: Path) -> None:
    """The setup page is about to write the configuration and answer: `il2ks run` must not restart the stack on that
    change before the answer has been sent (`finishing`)."""
    with contextlib.suppress(OSError):
        data_dir.mkdir(parents=True, exist_ok=True)
        finishing_path(data_dir).write_text("finishing\n", encoding="utf-8")


def clear_finishing(data_dir: Path) -> None:
    with contextlib.suppress(OSError):
        finishing_path(data_dir).unlink(missing_ok=True)


def finishing_path(data_dir: Path) -> Path:
    return data_dir / FINISHING_FILE


def finishing(data_dir: Path, *, now: Callable[[], float] = time.time) -> bool:
    """Whether a setup page submit is in flight. A marker older than `FINISHING_MAX_AGE_S` (a crashed request) is
    ignored, so a stale file can never block a restart for good."""
    try:
        age = now() - finishing_path(data_dir).stat().st_mtime
    except OSError:
        return False
    return age < FINISHING_MAX_AGE_S


def token_matches(data_dir: Path, given: str) -> bool:
    """Constant-time comparison with the pending token; False when none is pending."""
    expected = read_token(data_dir)
    return bool(expected) and secrets.compare_digest(expected.encode(), given.strip().encode())


def host_name(host_header: str) -> str:
    """The host part of a `Host` header: no port, no IPv6 brackets, lower case."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_direct_local_request(meta: Mapping[str, object]) -> bool:
    """Whether a WSGI request came straight from this machine's own browser.

    `meta` is `request.META`. The peer address (`REMOTE_ADDR`, set by the web server from the socket, never from a
    header) must be loopback; no header a reverse proxy adds may be present; the `Host` must be a local name (so a
    DNS-rebinding page cannot reach it under another name)."""
    remote = str(meta.get("REMOTE_ADDR", ""))
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_loopback:
        return False
    if any(meta.get(header) for header in FORWARDING_HEADERS):
        return False
    return host_name(str(meta.get("HTTP_HOST", ""))) in LOCAL_HOST_NAMES


def setup_url(host: str, port: int, token: str) -> str:
    """The address to open in a browser on this machine (a wildcard bind address means "this machine")."""
    shown = "localhost" if host in {"", "0.0.0.0", "::", "[::]"} else host
    if ":" in shown and not shown.startswith("["):
        shown = f"[{shown}]"
    return f"http://{shown}:{port}{SETUP_PATH}?token={token}"


class AttemptLimiter:
    """After `max_failures` wrong tokens the page refuses everything for `lockout_s` seconds.

    One counter for the whole process: every legitimate visitor is on this machine anyway, so per-address counting would
    add nothing. (With several web workers each has its own counter, which only makes guessing slower to stop; the token
    has 192 bits, so guessing is hopeless either way.)"""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        lockout_s: float = LOCKOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._lockout_s = lockout_s
        self._clock = clock
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def locked(self) -> bool:
        with self._lock:
            if self._locked_until and self._clock() >= self._locked_until:
                self._failures = 0
                self._locked_until = 0.0
            return self._locked_until > 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._max:
                self._locked_until = self._clock() + self._lockout_s

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._locked_until = 0.0
