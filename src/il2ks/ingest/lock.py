"""The single-writer lock (FR-ING-20).

`watch`, a scheduled `ingest` and `reprocess` take one exclusive lock before writing. The lock is an OS file lock on
`<data dir>/writer.lock` (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere). The OS drops it when the holder dies,
so a lock left by a crashed process is never "stuck": stale-lock detection is the OS's job, and the file content (PID,
host, command, start time) only serves the "who holds it" message. On Windows the lock covers one byte far past the
content, so other processes can still read the holder info while the lock is held.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self, cast

log = logging.getLogger(__name__)

LOCK_FILE = "writer.lock"
_WIN_LOCK_OFFSET = 1 << 30


class LockBusyError(RuntimeError):
    """Another writer holds the lock."""

    def __init__(self, path: Path, holder: LockHolder | None, *, what: str = "writer") -> None:
        self.path = path
        self.holder = holder
        who = holder.describe() if holder else "another process"
        super().__init__(f"another il2ks {what} is running ({who}); lock file {path}")


@dataclass(frozen=True, slots=True)
class LockHolder:
    pid: int
    host: str
    command: str
    since: str  # ISO 8601 UTC

    def describe(self) -> str:
        return f"'{self.command}', PID {self.pid} on {self.host}, since {self.since}"


def read_holder(path: Path) -> LockHolder | None:
    """Who wrote the lock file last, or None if it's empty or unreadable."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields = cast(dict[str, object], data)
    pid, host, command, since = fields.get("pid"), fields.get("host"), fields.get("command"), fields.get("since")
    if isinstance(pid, int) and isinstance(host, str) and isinstance(command, str) and isinstance(since, str):
        return LockHolder(pid, host, command, since)
    return None


def _try_lock(handle: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_WIN_LOCK_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_WIN_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_locked(path: Path) -> bool:
    """Whether some process holds the OS lock on this lock file right now (a probe: takes and drops it at once, and
    leaves the file content alone)."""
    try:
        handle = open(path, "r+b")  # noqa: SIM115 - closed in the finally below
    except OSError:
        return False  # no file, or none we can open: nobody we could see holds it
    try:
        if not _try_lock(handle):
            return True
        _unlock(handle)
        return False
    finally:
        handle.close()


class WriterLock:
    """Context manager. `wait=None`: fail at once with `LockBusyError`; else poll up to `wait` seconds.

    `file_name` and `what` let other "only one at a time" locks (`il2ks run`) reuse the same mechanism."""

    def __init__(
        self,
        data_dir: Path,
        command: str,
        wait: float | None = None,
        poll_s: float = 1.0,
        *,
        file_name: str = LOCK_FILE,
        what: str = "writer",
    ) -> None:
        self.path = data_dir / file_name
        self.what = what
        self.command = command
        self.wait = wait
        self.poll_s = poll_s
        self._handle: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        handle = os.fdopen(fd, "r+b")
        deadline = None if self.wait is None else time.monotonic() + self.wait
        while not _try_lock(handle):
            if deadline is None or time.monotonic() >= deadline:
                handle.close()
                raise LockBusyError(self.path, read_holder(self.path), what=self.what)
            time.sleep(self.poll_s)
        previous = read_holder(self.path)
        if previous is not None:
            # The OS lock was free, so whoever wrote this is gone (crash, kill, reboot).
            log.warning("replacing stale %s lock left by %s", self.what, previous.describe())
        holder = LockHolder(
            pid=os.getpid(),
            host=socket.gethostname(),
            command=self.command,
            since=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(dataclass_dict(holder)).encode("utf-8"))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            handle.truncate()  # an empty file means "released cleanly"
            handle.flush()
            _unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def dataclass_dict(holder: LockHolder) -> dict[str, object]:
    return {"pid": holder.pid, "host": holder.host, "command": holder.command, "since": holder.since}
