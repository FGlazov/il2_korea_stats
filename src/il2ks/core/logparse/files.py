"""Group log files into missions and read their lines (FR-ING-1, FR-ING-13).

Two inputs:
- raw DServer parts: `missionReport(<local start time>)[N].txt`, N = 0, 1, 2, ... (production);
- concatenated archives: one `.txt` or `.txt.zip` holding the whole mission, named after part `[0]`
  (history import, samples).
The mission UID is the timestamp inside the parentheses (doc 06).

A plain `missionReport(...)[0].txt` can be either a raw part 0 or a whole-mission archive; the names are identical.
The caller says which with `group_mission_files(..., txt_as=...)`: production reads parts (the default), history
import reads archives.
"""

import io
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.parser import ParseStats, parse_lines

type MissionLogKind = Literal["parts", "archive"]

LOG_ENCODING = "utf-8"
"""Logs are ASCII in practice (doc 12). Read as UTF-8 and replace undecodable bytes, so a stray byte never stops a
mission; it can at most turn one value into a replacement character."""

_NAME_RE = re.compile(
    r"missionReport\((\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\)\[(\d+)\]\.txt(\.zip)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MissionLog:
    """The files of one mission, in reading order."""

    mission_uid: str  # "2026-09-19_22-34-13"
    kind: MissionLogKind
    files: tuple[Path, ...]  # parts sorted by N, or exactly one archive


def mission_uid_from_name(name: str) -> str | None:
    """`missionReport(2026-09-19_22-34-13)[3].txt` -> `2026-09-19_22-34-13`; None if the name doesn't match."""
    m = _NAME_RE.fullmatch(name)
    return None if m is None else m.group(1)


def group_mission_files(paths: Iterable[Path], *, txt_as: MissionLogKind = "parts") -> list[MissionLog]:
    """Group files into missions, sorted by mission UID. Parts of one mission are sorted numerically by N.

    If both raw parts and an archive exist for the same UID, the raw parts win (they're newer than any archive).

    `txt_as` says what a plain `[0].txt` is: `"parts"` (raw DServer part 0, the default) or `"archive"` (a whole
    mission). A `[N].txt` with N >= 1 is always a part, and makes its mission's `[0].txt` a part too. `.txt.zip` is
    always an archive. With two archives for one UID (a `.txt` and a `.txt.zip`), the plain `.txt` is read (same
    content, no decompression). Names that don't match the pattern are ignored. The same path given twice counts once;
    two different paths with the same UID and N keep the first in path order."""
    parts: defaultdict[str, dict[int, Path]] = defaultdict(dict)
    plain_archives: dict[str, Path] = {}
    zip_archives: dict[str, Path] = {}
    zero_txt: dict[str, Path] = {}
    for path in sorted(set(paths)):
        m = _NAME_RE.fullmatch(path.name)
        if m is None:
            continue
        uid, index = m.group(1), int(m.group(2))
        if m.group(3) is not None:
            zip_archives.setdefault(uid, path)
        elif index == 0:
            zero_txt.setdefault(uid, path)
        else:
            parts[uid].setdefault(index, path)
    for uid, path in zero_txt.items():
        if txt_as == "parts" or uid in parts:
            parts[uid].setdefault(0, path)
        else:
            plain_archives[uid] = path
    logs: list[MissionLog] = []
    for uid in sorted(parts.keys() | plain_archives.keys() | zip_archives.keys()):
        if uid in parts:
            by_index = parts[uid]
            logs.append(MissionLog(uid, "parts", tuple(by_index[i] for i in sorted(by_index))))
        else:
            logs.append(MissionLog(uid, "archive", (plain_archives.get(uid) or zip_archives[uid],)))
    return logs


def _text_lines(stream: io.TextIOBase) -> Iterator[str]:
    for line in stream:
        yield line.rstrip("\r\n")


def _zip_member(archive: zipfile.ZipFile, path: Path) -> zipfile.ZipInfo:
    members = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".txt")]
    if len(members) != 1:
        raise ValueError(f"{path}: expected exactly one .txt inside, found {len(members)}")
    return members[0]


def _read_file_lines(path: Path) -> Iterator[str]:
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive, archive.open(_zip_member(archive, path)) as raw:
            yield from _text_lines(io.TextIOWrapper(raw, encoding=LOG_ENCODING, errors="replace", newline=None))
    else:
        with path.open(encoding=LOG_ENCODING, errors="replace", newline=None) as stream:
            yield from _text_lines(stream)


def read_mission_lines(log: MissionLog) -> Iterator[str]:
    """Yield the mission's lines in order, without line endings. Reads `.txt.zip` archives without extracting them.

    A UTF-8 byte order mark at the start of a file is dropped. Raises `ValueError` for a zip that doesn't hold
    exactly one `.txt`."""
    for path in log.files:
        first = True
        for line in _read_file_lines(path):
            if first:
                line = line.removeprefix("﻿")
                first = False
            yield line


def parse_mission(log: MissionLog, stats: ParseStats) -> Iterator[LogEvent]:
    """`parse_lines(read_mission_lines(log), stats)`."""
    return parse_lines(read_mission_lines(log), stats)
