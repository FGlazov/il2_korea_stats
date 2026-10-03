"""Log line -> typed event (FR-ING-3, TD-20).

CONTRACT STUB: the signatures are fixed, the bodies are iteration 1 work.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from il2ks.core.logparse.events import LogEvent

MAX_WARNINGS = 200
"""Stop collecting warning texts after this many per mission (they're still counted in `lines_bad`)."""


class ParseError(ValueError):
    """A line that can't be turned into an event (malformed or truncated)."""


@dataclass(slots=True)
class ParseStats:
    """Counters for one mission, recorded on `IngestRun` (FR-ING-11, TD-20)."""

    lines_total: int = 0
    lines_bad: int = 0
    log_version: int | None = None
    unknown_atypes: Counter[int] = field(default_factory=Counter[int])
    unknown_keys: Counter[str] = field(default_factory=Counter[str])  # "<atype>:<KEY>"
    warnings: list[str] = field(default_factory=list[str])


def parse_line(line: str) -> LogEvent:
    """Parse one line. Raises `ParseError` on a malformed line. Unknown ATypes give a `GenericEvent`, never an error."""
    raise NotImplementedError


def parse_lines(lines: Iterable[str], stats: ParseStats) -> Iterator[LogEvent]:
    """Parse lines lazily. Bad lines are counted and warned about in `stats`, never raised (FR-ING-3)."""
    raise NotImplementedError
