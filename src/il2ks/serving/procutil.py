"""Small process helpers: is a PID alive, "is `il2ks run` up" (the run lock), the `run.json` file with the children's
PIDs, and the Windows job object that takes the children down with `run`.

No psutil: it would be a dependency for a few functions. Never use `os.kill(pid, 0)` on Windows: there it terminates
the process instead of testing it.

"Is `il2ks run` up" is answered by an OS file lock that `run` holds for its whole life (`run.lock`, the mechanism of the
writer lock): the OS drops it when `run` dies however it dies, so after a crash or a reboot nothing is stale. A PID
check could not do that: PIDs are reused, so an old PID can belong to an unrelated process or even to the new `run`.
`run.json` is information only.
"""

import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import cast

from il2ks.ingest.lock import LockHolder, WriterLock, is_locked, read_holder

log = logging.getLogger("il2ks.run")

RUN_STATE_FILE = "run.json"
RUN_LOCK_FILE = "run.lock"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_alive(pid: int) -> bool:
    """Whether a process with this PID exists (and, on Windows, has not exited)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True, slots=True)
class RunState:
    """Written by `il2ks run` while it is up."""

    pid: int
    started: str  # ISO 8601 UTC
    children: dict[str, int]  # name -> PID at the time of writing (informational: children get restarted)


def run_state_path(data_dir: Path) -> Path:
    return data_dir / RUN_STATE_FILE


def write_run_state(data_dir: Path, children: dict[str, int]) -> None:
    state = {"pid": os.getpid(), "started": datetime.now(UTC).isoformat(timespec="seconds"), "children": children}
    run_state_path(data_dir).write_text(json.dumps(state), encoding="utf-8")


def clear_run_state(data_dir: Path) -> None:
    path = run_state_path(data_dir)
    try:
        state = read_run_state(data_dir)
        if state is None or state.pid == os.getpid():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def read_run_state(data_dir: Path) -> RunState | None:
    try:
        data: object = json.loads(run_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields = cast(dict[str, object], data)
    pid, started, children = fields.get("pid"), fields.get("started"), fields.get("children")
    if not isinstance(pid, int) or not isinstance(started, str) or not isinstance(children, dict):
        return None
    kids = {str(k): v for k, v in cast(dict[object, object], children).items() if isinstance(v, int)}
    return RunState(pid, started, kids)


def run_lock(data_dir: Path, *, wait: float | None = None) -> WriterLock:
    """The lock `il2ks run` holds while it is up. `wait`: how long to retry before giving up (a doctor probe may be
    holding it for a moment)."""
    return WriterLock(data_dir, "run", wait=wait, poll_s=0.2, file_name=RUN_LOCK_FILE, what="run")


def running_stack(data_dir: Path) -> LockHolder | None:
    """Who holds the run lock, if some `il2ks run` is up for this data dir (a holder whose info can't be read still
    counts: it comes back with PID 0)."""
    path = data_dir / RUN_LOCK_FILE
    if not is_locked(path):
        return None
    return read_holder(path) or LockHolder(pid=0, host="", command="run", since="")


def stop_on_signals(stop: threading.Event, *, on_close_wait_s: float = 4.5) -> Callable[[], None]:
    """Set `stop` on Ctrl+C, SIGTERM, Ctrl+Break and (Windows) console close / logoff / shutdown.

    Returns a function to call once shutdown is complete: the Windows close handler blocks until then (Windows kills the
    process when the handler returns, and allows only a few seconds), so the children are stopped first."""
    done = threading.Event()

    def handler(signum: int, frame: FrameType | None) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)
    on_windows_console_stop(stop.set, finished=done, wait_s=on_close_wait_s)
    return done.set


_ctrl_handler_keepalive: list[object] = []
_CTRL_BREAK_EVENT = 1
_CTRL_CLOSE_EVENTS = frozenset({2, 5, 6})  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT


def on_windows_console_stop(
    callback: Callable[[], None], *, finished: threading.Event | None = None, wait_s: float = 0.0
) -> None:
    """Windows only: call `callback` (on another thread) on Ctrl+Break, console close, logoff and shutdown.

    Why not `signal.signal(SIGBREAK, ...)`: Python runs a signal handler only when the main thread executes bytecode,
    and a main thread blocked in an untimed `Event.wait()` (granian's main loop) never does on Windows, so Ctrl+Break
    would be ignored. The callback only has to *wake* the main thread, e.g. by setting an event.

    On close/logoff/shutdown, Windows ends the process as soon as this handler returns, so with `finished` it blocks
    for up to `wait_s` seconds until the caller reports that shutdown is complete."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def on_event(event: int) -> bool:
        if event == _CTRL_BREAK_EVENT:
            callback()
            return True
        if event in _CTRL_CLOSE_EVENTS:
            callback()
            if finished is not None:
                finished.wait(wait_s)
            return True
        return False  # Ctrl+C: Python's own SIGINT handling deals with it

    handler = handler_type(on_event)
    _ctrl_handler_keepalive.append(handler)  # ctypes would free the callback otherwise
    ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleCtrlHandler(handler, True)


def terminate_as_keyboard_interrupt() -> None:
    """For single-purpose processes (`watch`): treat SIGTERM / Ctrl+Break like Ctrl+C, so the clean-up path runs.

    The main thread must wait in short slices for the handler to run (see `on_windows_console_stop`)."""

    def handler(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, handler)


# --- Windows job object -------------------------------------------------------------------------------------------

_job_keepalive: list[object] = []
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def kill_children_when_we_die() -> bool:
    """Windows: put this process in a job object that kills every member when the job's last handle closes.

    Children (and their children: web's workers, Caddy's helpers) inherit membership, so when `il2ks run` ends however
    it ends, even `schtasks /End` or the Task Manager, which never run its clean-up, nothing is left holding the ports.
    Doing it to ourselves before any child starts avoids the race of assigning each child after it started.
    The handle stays open for the life of the process; the OS closes it when we die. Returns whether it worked (it does
    on Windows 8 and later; elsewhere there is nothing to do and the answer is False)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_uint64) for name in "abcdef")

    class ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        log.warning("no job object (error %d): children will not stop with `run`", ctypes.get_last_error())
        return False
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
    ) or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        log.warning("job object setup failed (error %d): children will not stop with `run`", ctypes.get_last_error())
        kernel32.CloseHandle(job)
        return False
    _job_keepalive.append(job)
    return True
