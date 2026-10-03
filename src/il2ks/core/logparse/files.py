"""Group log files into missions and read their lines (FR-ING-1, FR-ING-13).

CONTRACT STUB: the signatures are fixed, the bodies are iteration 1 work.

Two inputs:
- raw DServer parts: `missionReport(<local start time>)[N].txt`, N = 0, 1, 2, ... (production);
- concatenated archives: one `.txt` or `.txt.zip` holding the whole mission, named after part `[0]`
  (history import, samples).
The mission UID is the timestamp inside the parentheses (doc 06).
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.parser import ParseStats

type MissionLogKind = Literal["parts", "archive"]


@dataclass(frozen=True, slots=True)
class MissionLog:
    """The files of one mission, in reading order."""

    mission_uid: str  # "2026-09-19_22-34-13"
    kind: MissionLogKind
    files: tuple[Path, ...]  # parts sorted by N, or exactly one archive


def mission_uid_from_name(name: str) -> str | None:
    """`missionReport(2026-09-19_22-34-13)[3].txt` -> `2026-09-19_22-34-13`; None if the name doesn't match."""
    raise NotImplementedError


def part_index(name: str) -> int | None:
    """`missionReport(...)[3].txt` -> 3; None for archives (`.txt.zip`) and non-matching names."""
    raise NotImplementedError


def group_mission_files(paths: Iterable[Path]) -> list[MissionLog]:
    """Group files into missions, sorted by mission UID. Parts of one mission are sorted numerically by N.

    If both raw parts and an archive exist for the same UID, the raw parts win (they're newer than any archive)."""
    raise NotImplementedError


def read_mission_lines(log: MissionLog) -> Iterator[str]:
    """Yield the mission's lines in order, without line endings. Reads `.txt.zip` archives without extracting them."""
    raise NotImplementedError


def parse_mission(log: MissionLog, stats: ParseStats) -> Iterator[LogEvent]:
    """`parse_lines(read_mission_lines(log), stats)`."""
    raise NotImplementedError
