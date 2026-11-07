"""`il2ks doctor` (FR-OPS-1): a registry of independent checks, each returning findings for the admin.

A check is a function `(cfg) -> Iterable[Finding]`, registered with `@check`. Checks never raise for a problem they
detect: they report it with a `fix` the admin can follow. Modules that define checks are imported by `run_checks`
(see `CHECK_MODULES`).
"""

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum

from il2ks.config import Config


class Level(IntEnum):
    OK = 0
    WARN = 1
    ERROR = 2


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    title: str  # one line, e.g. "Log folder has no mission reports"
    detail: str = ""  # what was checked / found
    fix: str = ""  # what the admin should do; empty for OK
    urgent: bool = False  # a warning that also gets the admin's red banner (an ERROR always does)

    @property
    def alert(self) -> bool:
        """Shown as a red banner on every admin page by the web server's own checks (`il2ks.web.server_checks`)."""
        return self.level is Level.ERROR or (self.urgent and self.level is Level.WARN)

    @property
    def is_error(self) -> bool:
        return self.level is Level.ERROR


type Check = Callable[[Config], Iterable[Finding]]

_CHECKS: list[Check] = []

CHECK_MODULES: tuple[str, ...] = (
    "il2ks.ops.checks",
    "il2ks.ops.serving_checks",
    "il2ks.ops.login_checks",
    "il2ks.ops.compression_check",
)
"""Modules whose import registers checks (each agent/area adds its module here)."""


def check(fn: Check) -> Check:
    """Register a doctor check (decorator)."""
    _CHECKS.append(fn)
    return fn


def run_checks(cfg: Config, *, skip: frozenset[str] = frozenset()) -> list[Finding]:
    """Import the check modules, run every check (but those named in `skip`), and turn a crashing check into an ERROR
    finding."""
    for name in CHECK_MODULES:
        importlib.import_module(name)
    findings: list[Finding] = []
    for fn in _CHECKS:
        if fn.__name__ in skip:
            continue
        try:
            findings.extend(fn(cfg))
        except Exception as exc:  # a broken check must not hide the others
            findings.append(Finding(Level.ERROR, f"Check {fn.__name__} crashed", repr(exc), "Report this as a bug."))
    return findings
