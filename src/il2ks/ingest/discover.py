"""Find missions in the log folder and decide what to do with each one (FR-ING-1, FR-ING-2, FR-ING-16, FR-ING-18, FR-ING-19).

Pure decisions (completeness, fingerprint, history classification) take plain values, so tests control time and files.
Reading `IngestRun` history from the DB is the runner's job.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from il2ks.config import IngestConfig
from il2ks.core.logparse.files import MissionLog

type Completeness = Literal["mission_end", "newer_mission", "idle", "archive", "import"]
"""Why a mission counts as complete. `import`: an explicit `ingest --from`, where every mission is taken as complete."""

type Decision = Literal["new", "changed", "retry", "unchanged", "backoff", "gave_up"]
"""`new`/`changed`/`retry` get ingested; `unchanged`/`backoff`/`gave_up` are skipped (FR-ING-18, FR-ING-19)."""

INGEST_DECISIONS: frozenset[Decision] = frozenset({"new", "changed", "retry"})
MISSION_END_SCAN_PARTS = 3
"""AType 7 is looked for in the last this-many parts only (cleanup lines after it rarely fill more than a part or two)."""

_MISSION_END = re.compile(rb"AType:7(?!\d)")

type Grouper = Callable[[Iterable[Path]], list[MissionLog]]


@dataclass(frozen=True, slots=True)
class FileState:
    path: Path
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> FileState:
        st = path.stat()
        return cls(path, st.st_size, st.st_mtime_ns)

    @property
    def mtime(self) -> float:
        return self.mtime_ns / 1e9


def fingerprint(files: Sequence[FileState]) -> str:
    """sha256 over (name, size, mtime) of the source files, in order (FR-ING-18). Folder-independent: names only."""
    digest = hashlib.sha256()
    for f in files:
        digest.update(f"{f.path.name}\0{f.size}\0{f.mtime_ns}\n".encode())
    return digest.hexdigest()


def has_mission_end(paths: Sequence[Path]) -> bool:
    """AType 7 appears in one of the last `MISSION_END_SCAN_PARTS` parts (raw byte search, no parsing)."""
    for path in paths[-MISSION_END_SCAN_PARTS:]:
        try:
            if _MISSION_END.search(path.read_bytes()):
                return True
        except OSError:
            continue
    return False


def completeness(
    log: MissionLog,
    files: Sequence[FileState],
    *,
    newer_mission_exists: bool,
    now: float,
    cfg: IngestConfig,
    remote: bool,
) -> Completeness | None:
    """FR-ING-2: complete = AType 7 seen, or a newer mission's `[0]` exists, or no new part for N minutes.

    - AType 7 also waits `settle_seconds` after the newest write: cleanup lines follow the end marker (doc 12 Timing).
    - Remote mode (FR-ING-16): every part must be unmodified for `stable_seconds` first, since a newer mission existing
      doesn't prove the copy of this one finished. Late or re-copied parts are caught by the fingerprint (FR-ING-18).
    `now` is a POSIX timestamp."""
    if log.kind == "archive":
        return "archive"
    if not files:
        return None
    newest_age = now - max(f.mtime for f in files)
    if remote and newest_age < cfg.stable_seconds:
        return None
    if newer_mission_exists:
        return "newer_mission"
    if newest_age >= cfg.idle_minutes * 60:
        return "idle"
    if newest_age >= cfg.settle_seconds and has_mission_end([f.path for f in files]):
        return "mission_end"
    return None


@dataclass(frozen=True, slots=True)
class LastRun:
    """The latest `IngestRun` of a mission, as far as discovery cares."""

    status: Literal["ok", "failed", "skipped"]
    fingerprint: str
    attempts: int
    next_retry_at: datetime | None
    il2ks_version: str


def classify(fp: str, last: LastRun | None, *, version: str, now: datetime) -> Decision:
    """New, changed, retry-due or skipped, from the mission's run history (FR-ING-18, FR-ING-19).

    A failed mission is retried when its backoff time has come, and also at once when its files changed or a different
    il2ks version is running. After the last backoff step (`next_retry_at` is empty) it stays failed until one of those
    happens or the admin runs `il2ks reprocess --mission`."""
    if last is None:
        return "new"
    if last.status == "ok" or last.status == "skipped":
        return "unchanged" if last.fingerprint == fp else "changed"
    if last.fingerprint != fp or last.il2ks_version != version:
        return "retry"
    if last.next_retry_at is None:
        return "gave_up"
    return "retry" if last.next_retry_at <= now else "backoff"


def next_retry(attempts: int, backoff: Sequence[timedelta], now: datetime) -> datetime | None:
    """After failed attempt number `attempts` (1-based): when to try again, or None to stop (FR-ING-19)."""
    if 1 <= attempts <= len(backoff):
        return now + backoff[attempts - 1]
    return None


@dataclass(frozen=True, slots=True)
class Found:
    """One mission seen in a folder, with its files and completeness."""

    log: MissionLog
    files: tuple[FileState, ...]
    fingerprint: str
    complete: Completeness | None

    @property
    def mission_uid(self) -> str:
        return self.log.mission_uid


def list_log_files(folder: Path) -> list[Path]:
    """Files directly in `folder` (not recursive). Names are filtered by `group_mission_files`."""
    return sorted(p for p in folder.iterdir() if p.is_file())


def discover(
    paths: Iterable[Path],
    group: Grouper,
    *,
    now: float,
    cfg: IngestConfig,
    remote: bool,
    treat_all_complete: bool = False,
) -> list[Found]:
    """Group files into missions and decide completeness for each, oldest first.

    `treat_all_complete`: `ingest --from` imports, where there's no writer to wait for."""
    logs = sorted(group(paths), key=lambda m: m.mission_uid)
    found: list[Found] = []
    for i, log in enumerate(logs):
        try:
            files = tuple(FileState.of(p) for p in log.files)
        except FileNotFoundError:
            continue  # moved or deleted while we looked; next run sees the new state
        newer = any(_has_first_part(later) for later in logs[i + 1 :])
        if treat_all_complete:
            complete: Completeness | None = "import"
        else:
            complete = completeness(log, files, newer_mission_exists=newer, now=now, cfg=cfg, remote=remote)
        found.append(Found(log, files, fingerprint(files), complete))
    return found


def _has_first_part(log: MissionLog) -> bool:
    """A newer mission has started: its `[0]` part exists (or it's a whole-mission archive)."""
    return log.kind == "archive" or any(p.name.endswith("[0].txt") for p in log.files)
