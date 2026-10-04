"""Helpers for the online-now tests: a fixture mission cut into growing raw parts, written to a temp log folder."""

import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from tests.conftest import FIXTURE_LOGS

LIVE_UID = "2026-09-19_21-00-00"


def fixture_lines(name: str = "typical") -> list[str]:
    """All lines of an anonymized fixture mission (never real data, see tests/fixtures)."""
    with zipfile.ZipFile(FIXTURE_LOGS / f"{name}.txt.zip") as archive:
        (member,) = archive.namelist()
        return archive.read(member).decode("utf-8").splitlines()


def split_into_parts(lines: list[str], count: int) -> list[list[str]]:
    """Cut a mission into `count` raw parts at AType 15 header lines, the way DServer starts a new part."""
    headers = [i for i, line in enumerate(lines) if "AType:15" in line]
    cuts = [headers[len(headers) * k // count] for k in range(count)]
    cuts[0] = 0
    return [lines[a:b] for a, b in zip(cuts, [*cuts[1:], len(lines)], strict=True)]


def part_path(folder: Path, uid: str, index: int) -> Path:
    return folder / f"missionReport({uid})[{index}].txt"


def write_part(
    folder: Path, uid: str, index: int, lines: list[str], when: datetime, *, newline_at_end: bool = True
) -> Path:
    """Write (replace) one part and stamp it `when`. `newline_at_end=False` leaves the last line unfinished."""
    folder.mkdir(parents=True, exist_ok=True)
    path = part_path(folder, uid, index)
    text = "\r\n".join(lines) + ("\r\n" if newline_at_end else "")
    path.write_bytes(text.encode("utf-8"))
    stamp = (when - timedelta(seconds=5)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def mission_end_index(lines: list[str]) -> int:
    """Index of the first AType 7 line."""
    return next(i for i, line in enumerate(lines) if " AType:7" in line)


def free_clock() -> float:
    """A cost clock for `LiveTracker` under which every pass costs nothing: the CPU cap never backs the intervals off,
    whatever the speed of the machine running the test."""
    return 0.0


class SteppingClock:
    """A cost clock where each reading is `step` seconds after the last: every measured pass costs exactly `step`."""

    def __init__(self, step: float) -> None:
        self.step = step
        self._now = 0.0

    def __call__(self) -> float:
        self._now += self.step
        return self._now
