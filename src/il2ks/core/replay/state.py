"""The streaming replay state machine (TD-07).

CONTRACT STUB: the signatures are fixed, the bodies are iteration 1 work.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from il2ks.core.catalog.loader import Catalog
from il2ks.core.logparse.events import LogEvent
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult, SortieResult


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A provisional view of an in-progress mission (it2 "online now"). Unresolved fields may change."""

    tick: int
    sorties: tuple[SortieResult, ...]


class Replay:
    """Feed events one at a time, then `finish()` (TD-07). Deterministic: same events in, same result out."""

    def __init__(self, catalog: Catalog, rules: ReplayRules | None = None) -> None:
        raise NotImplementedError

    def feed(self, event: LogEvent) -> None:
        raise NotImplementedError

    def snapshot(self) -> Snapshot:
        raise NotImplementedError

    def finish(self) -> MissionResult:
        raise NotImplementedError


def run(events: Iterable[LogEvent], catalog: Catalog, rules: ReplayRules | None = None) -> MissionResult:
    """Batch mode: feed every event, then finish."""
    replay = Replay(catalog, rules)
    for event in events:
        replay.feed(event)
    return replay.finish()
