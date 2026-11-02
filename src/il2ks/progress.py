"""The progress of a long run on a terminal: one line that updates in place, the last warnings and errors under it.

Maintainer 2026-11-02: "add an eta for long running jobs of at least 200 missions? and dont create new lines, update a
single line (with perhaps a log feed below for warnings/errors)". `live_progress` shows it from `LIVE_MIN_MISSIONS`
missions, on a real terminal only: a shorter run, a pipe, a service and a console without escape codes keep their log
lines. The log file gets every line either way (`logsetup.live_stdout`).
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TextIO

from il2ks import logsetup

LIVE_MIN_MISSIONS = 200
"""From this many missions in one run, the terminal shows the live line."""

MIN_DONE_FOR_ETA = 5
"""Missions done before the line gives an estimate: the first ones say little."""

FEED_LINES = 8
"""Warnings and errors kept under the line. The older ones are counted, and are in the log file."""

_BACK = "\x1b[{rows}F"  # the cursor up that many rows, to the start of the row
_CLEAR_BELOW = "\x1b[J"


def enable_windows_vt() -> bool:
    """Switch the Windows console to understand ANSI escape codes (Windows 10 and later)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def format_duration(seconds: float) -> str:
    """`45s`, `1m 22s`, `2h 05m`."""
    minutes, rest = divmod(max(round(seconds), 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {rest:02d}s" if minutes else f"{rest}s"


def status_text(done: int, total: int, elapsed: float) -> str:
    """`Mission 50/210 ETA: 1m 22s`. The rate is that of the whole run so far, so a level-2 pass counts as part of it
    and the estimate does not jump around it. The rebuild after the last mission is not in it."""
    eta = format_duration(elapsed / done * (total - done)) if done >= MIN_DONE_FOR_ETA else "estimating"
    return f"Mission {done}/{total} ETA: {eta}"


class LiveProgress:
    """The line and the feed under it. A draw takes the cursor back over the last block, clears it and writes the new
    one, so nothing scrolls. Lines are cut to the window's width and the block to its height: a wrapped row or a block
    taller than the window would send the cursor back to the wrong place."""

    def __init__(self, total: int, stream: TextIO, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._total = total
        self._stream = stream
        self._clock = clock
        self._started = clock()
        self._done = 0
        self._phase = ""
        self._feed: deque[str] = deque(maxlen=FEED_LINES)
        self._dropped = 0
        self._drawn = 0
        self._lock = threading.RLock()  # the log handler can call from any thread

    def advance(self, done: int) -> None:
        with self._lock:
            self._done = done
            self._phase = ""
            self._draw(status_text(done, self._total, self._clock() - self._started))

    def phase(self, text: str) -> None:
        """Something that takes a while between two missions; the next mission clears it."""
        with self._lock:
            self._phase = text
            self._draw(status_text(self._done, self._total, self._clock() - self._started))

    def feed(self, text: str) -> None:
        with self._lock:
            if len(self._feed) == FEED_LINES:
                self._dropped += 1
            self._feed.append(text)
            self._draw(status_text(self._done, self._total, self._clock() - self._started))

    def close(self) -> None:
        with self._lock:
            self._phase = ""
            if self._done == self._total:
                took = format_duration(self._clock() - self._started)
                self._draw(f"Mission {self._done}/{self._total} done in {took}")
            else:
                self._draw(f"Mission {self._done}/{self._total} stopped")

    def _draw(self, status: str) -> None:
        size = shutil.get_terminal_size()
        lines = [f"{status} ({self._phase})" if self._phase else status]
        if self._dropped:
            lines.append(f"... {self._dropped} earlier, see the log file")
        lines.extend(self._feed)
        lines = [line[: max(size.columns - 1, 1)] for line in lines[: max(size.lines - 1, 1)]]
        back = _BACK.format(rows=self._drawn) if self._drawn else ""
        self._stream.write(back + _CLEAR_BELOW + "".join(f"{line}\n" for line in lines))
        self._stream.flush()
        self._drawn = len(lines)


def _can_redraw(stream: TextIO) -> bool:
    return stream.isatty() and os.environ.get("TERM") != "dumb" and enable_windows_vt()


@contextmanager
def live_progress(total: int, stream: TextIO | None = None) -> Generator[Callable[[int], None]]:
    """Yields `advance(done)`, to call after each mission; it does nothing when the run keeps its log lines."""
    out = sys.stdout if stream is None else stream
    if total < LIVE_MIN_MISSIONS or not _can_redraw(out):
        yield lambda _done: None
        return
    display = LiveProgress(total, out)
    with logsetup.live_stdout(display):
        display.advance(0)
        try:
            yield display.advance
        finally:
            display.close()
