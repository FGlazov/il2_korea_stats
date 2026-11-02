"""The progress of a long run on a terminal (maintainer 2026-11-02): "add an eta for long running jobs of at least 200
missions? and dont create new lines, update a single line (with perhaps a log feed below for warnings/errors)"."""

import io
import json
import logging
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from il2ks import progress
from il2ks.logsetup import configure_logging, live_stdout
from il2ks.progress import (
    FEED_LINES,
    LIVE_MIN_MISSIONS,
    LiveProgress,
    format_duration,
    live_progress,
    status_text,
)


def screen(output: str) -> list[str]:
    """What a terminal shows after `output`, for the three things the display writes: text, newline, and the two
    escapes that take the cursor back and clear from there down."""
    lines: list[str] = []
    row = 0
    for part in re.split(r"(\x1b\[\d+F|\x1b\[J|\n)", output):
        if part == "\n":
            row += 1
        elif part == "\x1b[J":
            del lines[row:]
        elif part.startswith("\x1b["):
            row -= int(part[2:-1])
        elif part:
            lines.extend("" for _ in range(row + 1 - len(lines)))
            lines[row] = part
    return lines


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((200, 50)))


@pytest.fixture(autouse=True)
def no_log_handlers() -> Iterator[None]:
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    root.handlers[:] = before
    root.setLevel(level)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0s"), (45.4, "45s"), (82, "1m 22s"), (3600, "1h 00m"), (7500, "2h 05m"), (-3, "0s")],
)
def test_a_duration_reads_like_a_person_says_it(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


def test_the_status_has_the_format_the_maintainer_asked_for() -> None:
    assert status_text(50, 210, 25.625) == "Mission 50/210 ETA: 1m 22s"


def test_the_first_missions_give_no_estimate() -> None:
    assert status_text(0, 210, 0) == "Mission 0/210 ETA: estimating"
    assert status_text(progress.MIN_DONE_FOR_ETA - 1, 210, 3) == "Mission 4/210 ETA: estimating"


@pytest.mark.usefixtures("wide")
def test_every_mission_updates_one_line() -> None:
    out, clock = Terminal(), Clock()
    display = LiveProgress(210, out, clock=clock)

    for done in range(1, 51):
        clock.now += 0.5125
        display.advance(done)

    assert screen(out.getvalue()) == ["Mission 50/210 ETA: 1m 22s"]


@pytest.mark.usefixtures("wide")
def test_warnings_and_errors_stay_under_the_line() -> None:
    out, clock = Terminal(), Clock()
    display = LiveProgress(210, out, clock=clock)

    display.advance(1)
    display.feed("WARNING one")
    display.feed("ERROR two")
    display.advance(2)

    assert screen(out.getvalue()) == ["Mission 2/210 ETA: estimating", "WARNING one", "ERROR two"]


@pytest.mark.usefixtures("wide")
def test_the_feed_keeps_the_last_lines_and_counts_the_rest() -> None:
    out = Terminal()
    display = LiveProgress(210, out, clock=Clock())

    for n in range(FEED_LINES + 3):
        display.feed(f"WARNING {n}")

    shown = screen(out.getvalue())
    assert shown[1] == "... 3 earlier, see the log file"
    assert shown[2:] == [f"WARNING {n}" for n in range(3, FEED_LINES + 3)]


def test_a_long_line_is_cut_so_it_cannot_wrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrapped line is two rows, and the display would go back one row too few."""
    monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((20, 50)))
    out = Terminal()
    display = LiveProgress(210, out, clock=Clock())

    display.feed("WARNING " + "x" * 100)

    assert all(len(line) < 20 for line in screen(out.getvalue()))


def test_the_block_never_outgrows_a_small_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Going back further than the top of the window would leave the old block on screen."""
    monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((80, 5)))
    out = Terminal()
    display = LiveProgress(210, out, clock=Clock())

    for n in range(FEED_LINES):
        display.feed(f"WARNING {n}")

    assert len(screen(out.getvalue())) == 4


@pytest.mark.usefixtures("wide")
def test_the_end_says_how_long_it_took_and_leaves_the_cursor_below() -> None:
    out, clock = Terminal(), Clock()
    display = LiveProgress(200, out, clock=clock)

    clock.now += 185
    display.advance(200)
    display.close()
    out.write("next line\n")

    assert screen(out.getvalue()) == ["Mission 200/200 done in 3m 05s", "next line"]


@pytest.mark.usefixtures("wide")
def test_a_run_cut_short_keeps_its_count() -> None:
    out = Terminal()
    display = LiveProgress(200, out, clock=Clock())

    display.advance(7)
    display.close()

    assert screen(out.getvalue()) == ["Mission 7/200 stopped"]


@pytest.mark.usefixtures("wide")
def test_a_pass_that_takes_a_while_shows_on_the_line_until_the_next_mission() -> None:
    out = Terminal()
    display = LiveProgress(210, out, clock=Clock())

    display.advance(1)
    display.phase("level 2 for 1/210 missions")
    assert screen(out.getvalue()) == ["Mission 1/210 ETA: estimating (level 2 for 1/210 missions)"]

    display.advance(2)
    assert screen(out.getvalue()) == ["Mission 2/210 ETA: estimating"]


def test_a_short_run_keeps_its_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "enable_windows_vt", lambda: True)
    out = Terminal()

    with live_progress(LIVE_MIN_MISSIONS - 1, out) as advance:
        advance(1)

    assert out.getvalue() == ""


def test_a_pipe_or_a_service_keeps_its_log_lines() -> None:
    out = io.StringIO()

    with live_progress(LIVE_MIN_MISSIONS, out) as advance:
        advance(1)

    assert out.getvalue() == ""


def test_a_console_without_escapes_keeps_its_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "enable_windows_vt", lambda: False)
    out = Terminal()

    with live_progress(LIVE_MIN_MISSIONS, out) as advance:
        advance(1)

    assert out.getvalue() == ""


class Recording:
    def __init__(self) -> None:
        self.fed: list[str] = []
        self.phases: list[str] = []

    def feed(self, text: str) -> None:
        self.fed.append(text)

    def phase(self, text: str) -> None:
        self.phases.append(text)


def day_file(tmp_path: Path) -> list[dict[str, str]]:
    logging.shutdown()
    (path,) = tmp_path.glob("reprocess-*.log")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_console_goes_quiet_and_the_log_file_keeps_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("reprocess", tmp_path)
    log = logging.getLogger("il2ks.test")
    display = Recording()

    with live_stdout(display):
        log.info("m1: 1 of 200 missions done, 199 left")
        log.warning("something odd")
        log.info("level 2 for 20/200 missions", extra={"phase": True})
    log.info("back to normal")

    printed = capsys.readouterr().out
    assert "1 of 200" not in printed
    assert "something odd" not in printed
    assert "back to normal" in printed
    assert display.fed == ["WARNING something odd"]
    assert display.phases == ["level 2 for 20/200 missions"]
    assert [entry["message"] for entry in day_file(tmp_path)] == [
        "m1: 1 of 200 missions done, 199 left",
        "something odd",
        "level 2 for 20/200 missions",
        "back to normal",
    ]


def test_an_error_with_a_traceback_is_one_line_with_its_cause(tmp_path: Path) -> None:
    configure_logging("reprocess", tmp_path)
    log = logging.getLogger("il2ks.test")
    display = Recording()

    with live_stdout(display):
        log.error("m1: reprocess failed:\nTraceback (most recent call last):\n  File x\nValueError: bad line")
        try:
            raise KeyError("missing")
        except KeyError:
            log.exception("m2 failed")

    assert display.fed == [
        "ERROR m1: reprocess failed: ValueError: bad line",
        "ERROR m2 failed KeyError: 'missing'",
    ]


def test_the_console_comes_back_after_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("reprocess", tmp_path)

    with pytest.raises(RuntimeError), live_stdout(Recording()):
        raise RuntimeError

    logging.getLogger("il2ks.test").warning("printed again")
    assert "printed again" in capsys.readouterr().out
