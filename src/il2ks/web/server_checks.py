"""`il2ks doctor` inside the web server (FR-OPS-1), so the admin sees problems without a command line.

Every web worker runs the doctor checks in a daemon thread, `FIRST_DELAY_S` after it starts (Caddy and the database
are up by then) and every `INTERVAL_S` after that, and keeps the newest result in memory. A request never waits for a
check: `Il2ksAdminSite.each_context` only reads `latest()`. The admin shows the alerts (every ERROR and the warnings a
check marks `urgent`: debug mode, an old Caddy) as a red banner on every page, and all warnings on the home page.

Left out here: `ports`, because a running server holds its own ports and the check would report them as taken.
Each worker runs its own checks (they are cheap and read only); alerts are logged when they change.
"""

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.db import connections
from django.utils import timezone

from il2ks.config import Config
from il2ks.ops.doctor import Finding, run_checks

log = logging.getLogger("il2ks.web")

FIRST_DELAY_S = 30.0
INTERVAL_S = 3600.0
SKIPPED = frozenset({"ports"})


@dataclass(frozen=True, slots=True)
class Result:
    at: datetime
    findings: tuple[Finding, ...]

    @property
    def alerts(self) -> list[Finding]:
        """For the red banner, errors first."""
        return sorted((f for f in self.findings if f.alert), key=lambda f: -f.level)

    @property
    def problems(self) -> list[Finding]:
        """Every ERROR and WARN, worst first (the admin home page)."""
        return sorted((f for f in self.findings if f.level), key=lambda f: -f.level)


_latest: Result | None = None
_thread: threading.Thread | None = None
_stop: threading.Event | None = None
_lock = threading.Lock()


def latest() -> Result | None:
    """The newest result of this process; None before the first run."""
    return _latest


def run_once(cfg: Config, *, checks: Callable[[Config], list[Finding]] | None = None) -> Result:
    """Run the checks, keep the result, log the alerts when they differ from the last run's."""
    global _latest
    findings = (checks or _server_checks)(cfg)
    result = Result(timezone.now(), tuple(findings))
    before: set[str] = {f.title for f in _latest.alerts} if _latest is not None else set()
    with _lock:
        _latest = result
    for finding in result.alerts:
        if finding.title not in before:
            log.warning("doctor: %s. %s %s", finding.title, finding.detail, finding.fix)
    return result


def _server_checks(cfg: Config) -> list[Finding]:
    return run_checks(cfg, skip=SKIPPED)


def enabled() -> bool:
    """`IL2KS_NO_POLLER=1` keeps this thread off too, like the front-page image poller (tests that start workers)."""
    return os.environ.get("IL2KS_NO_POLLER", "") == ""


def start(
    cfg: Config,
    *,
    first_delay_s: float = FIRST_DELAY_S,
    interval_s: float = INTERVAL_S,
    checks: Callable[[Config], list[Finding]] | None = None,
) -> threading.Thread | None:
    """Start the thread (once per process; a second call does nothing). A failing run is logged and tried again at
    the next interval."""
    global _thread, _stop
    with _lock:
        if _thread is not None and _thread.is_alive():
            return None
        stop = _stop = threading.Event()

        def loop() -> None:
            delay = first_delay_s
            while not stop.wait(delay):
                delay = interval_s
                try:
                    run_once(cfg, checks=checks)
                except Exception:
                    log.exception("doctor checks in the web server failed")
                finally:
                    connections.close_all()  # this thread's connections only; it sleeps for an hour

        _thread = threading.Thread(target=loop, name="il2ks-doctor", daemon=True)
        _thread.start()
        return _thread


def reset() -> None:
    """Stop the thread and forget the result (tests)."""
    global _latest, _thread
    with _lock:
        thread, _thread = _thread, None
        if _stop is not None:
            _stop.set()
    if thread is not None:
        thread.join(5)
    with _lock:
        _latest = None
