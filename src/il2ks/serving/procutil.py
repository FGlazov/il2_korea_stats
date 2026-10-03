"""Small process helpers: is a PID alive, and the `run.json` file that says "`il2ks run` is up" (doctor, guard).

No psutil: it would be a dependency for two functions. Never use `os.kill(pid, 0)` on Windows: there it terminates
the process instead of testing it.
"""

import json
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

RUN_STATE_FILE = "run.json"
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


def running_stack(data_dir: Path) -> RunState | None:
    """The `il2ks run` that is alive for this data dir, if any."""
    state = read_run_state(data_dir)
    return state if state is not None and pid_alive(state.pid) else None


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
