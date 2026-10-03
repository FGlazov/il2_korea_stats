"""Find DServer's text log folder for `il2ks setup` (FR-OPS-1).

The exact place Korea's DServer writes its text logs is not known (OQ-1), so this looks for what a log folder is: a
folder holding `missionReport(<date>)[<n>].txt` files. The search only visits a short list of likely roots (Program
Files, game library folders, the user's home, a Wine prefix) and, below them, only branches whose names look like IL-2
installs, within a depth, entry and time budget. It never walks a whole drive.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from il2ks.core.logparse.files import mission_uid_from_name

MAX_DEPTH = 7
MAX_ENTRIES = 40_000
TIME_BUDGET_S = 5.0
_GAME_NAME_RE = re.compile(r"il-?2|sturmovik|dserver|great.?battles|korea|steam|games?|bos|wine|lutris", re.IGNORECASE)
_SKIP_NAMES = frozenset({"windows", "$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__"})


@dataclass(frozen=True, slots=True)
class LogFolder:
    path: Path
    reports: int  # missionReport(...)[n].txt files directly inside
    newest_mtime: float


_DRIVE_FIXED = 3  # GetDriveTypeW: a local disk (not removable, network, CD, RAM)


def is_fixed_drive(letter: str) -> bool:
    """Whether `D:` is a local fixed disk. Asked before touching a drive: probing a disconnected network drive or an
    empty card reader can stall for many seconds, and `GetDriveTypeW` itself does not touch the medium."""
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    return kernel32.GetDriveTypeW(f"{letter}:\\") == _DRIVE_FIXED


def default_roots(
    platform: str,
    env: Mapping[str, str],
    home: Path,
    *,
    fixed_drive: Callable[[str], bool] = is_fixed_drive,
) -> Iterator[Path]:
    """Where an IL-2 install is likely to be, most specific first. Only existing folders are produced.

    Lazy: each candidate is probed when the caller asks for it, so the caller's time budget already runs while a slow
    disk is being probed."""
    seen: set[Path] = set()
    for path in _candidates(platform, env, home, fixed_drive):
        if path not in seen and _is_dir(path):
            seen.add(path)
            yield path


def _candidates(
    platform: str, env: Mapping[str, str], home: Path, fixed_drive: Callable[[str], bool]
) -> Iterator[Path]:
    candidates: list[Path] = []
    if platform == "win32":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemDrive"):
            if env.get(var):
                base = Path(env[var] + ("\\" if var == "SystemDrive" else ""))
                candidates.append(base)
                if var == "ProgramFiles(x86)":
                    candidates.append(base / "Steam" / "steamapps" / "common")
        candidates += [home, home / "Desktop", home / "Documents", home / "Games"]
        candidates += [Path(f"{letter}:\\") for letter in "DEFG" if fixed_drive(letter)]  # game servers; top level only
    else:
        prefixes = [Path(env["WINEPREFIX"])] if env.get("WINEPREFIX") else []
        prefixes += sorted(home.glob(".wine*"))[:5]
        prefixes += sorted(home.glob(".local/share/Steam/steamapps/compatdata/*/pfx"))[:5]
        for prefix in prefixes:
            drive_c = prefix / "drive_c"
            candidates += [
                drive_c / "Program Files",
                drive_c / "Program Files (x86)",
                drive_c / "Games",
                drive_c / "users",
                drive_c,
            ]
        candidates += [home / "Games", home / ".local" / "share" / "Steam" / "steamapps" / "common", home]
    yield from candidates


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def inspect_folder(path: Path, spent: Callable[[], bool] | None = None) -> LogFolder | None:
    """`LogFolder` if `path` directly holds mission report text files, else None.

    `spent` (the search budget) is asked once per directory entry read; when it says the budget is used up the folder
    is judged by what was read so far, so a folder with a huge number of files can't blow the budget."""
    reports = 0
    newest = 0.0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if spent is not None and spent():
                    break
                if entry.name.lower().endswith(".txt") and mission_uid_from_name(entry.name) is not None:
                    reports += 1
                    newest = max(newest, entry.stat().st_mtime)
    except OSError:
        return None
    return LogFolder(path, reports, newest) if reports else None


class _Budget:
    def __init__(self, max_entries: int, seconds: float, clock: Callable[[], float]) -> None:
        self._left = max_entries
        self._deadline = clock() + seconds
        self._clock = clock

    def spent(self) -> bool:
        self._left -= 1
        return self._left < 0 or self._clock() > self._deadline


def _walk(root: Path, *, max_depth: int, budget: _Budget) -> Iterator[Path]:
    """Directories below `root`, breadth first, following only game-looking names below the first level."""
    level = [(root, 0)]
    while level:
        following: list[tuple[Path, int]] = []
        for folder, depth in level:
            yield folder
            if depth >= max_depth:
                continue
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if budget.spent():
                            return
                        name = entry.name.lower()
                        if name in _SKIP_NAMES or not entry.is_dir(follow_symlinks=False):
                            continue
                        # Directly under a root, only branches named like a game install; below those, everything.
                        if depth > 0 or _GAME_NAME_RE.search(name):
                            following.append((Path(entry.path), depth + 1))
            except OSError:
                continue
        level = following


def find_log_folders(
    roots: Iterable[Path],
    *,
    max_depth: int = MAX_DEPTH,
    max_entries: int = MAX_ENTRIES,
    seconds: float = TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> list[LogFolder]:
    """Folders under `roots` that hold mission reports, newest report first.

    Search cost is bounded: `max_entries` directory entries and `seconds` in total, across all roots."""
    budget = _Budget(max_entries, seconds, clock)
    found: dict[Path, LogFolder] = {}
    for root in roots:
        for folder in _walk(root, max_depth=max_depth, budget=budget):
            hit = inspect_folder(folder, budget.spent)
            if hit is not None:
                found[hit.path] = hit
        if budget.spent():
            break
    return sorted(found.values(), key=lambda f: f.newest_mtime, reverse=True)
