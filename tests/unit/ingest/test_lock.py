"""The single-writer lock (FR-ING-20)."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from il2ks.ingest.lock import LOCK_FILE, LockBusyError, WriterLock, read_holder


def test_second_writer_is_refused_with_a_message_naming_the_holder(tmp_path: Path) -> None:
    with WriterLock(tmp_path, "watch"):
        with pytest.raises(LockBusyError) as info, WriterLock(tmp_path, "ingest"):
            pass
        message = str(info.value)
        assert "watch" in message
        assert "PID" in message
        assert str(tmp_path / LOCK_FILE) in message


def test_lock_is_free_again_after_release(tmp_path: Path) -> None:
    with WriterLock(tmp_path, "ingest"):
        pass
    assert read_holder(tmp_path / LOCK_FILE) is None
    with WriterLock(tmp_path, "reprocess"):
        pass


def test_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), WriterLock(tmp_path, "ingest"):
        raise RuntimeError("boom")
    with WriterLock(tmp_path, "ingest"):
        pass


def test_waiting_gives_up_after_the_timeout(tmp_path: Path) -> None:
    with WriterLock(tmp_path, "watch"):
        lock = WriterLock(tmp_path, "ingest", wait=0.2, poll_s=0.05)
        with pytest.raises(LockBusyError):
            lock.acquire()


def test_stale_lock_file_of_a_dead_process_is_taken_over(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A crash leaves the file with the old holder's info but no OS lock (the OS drops it with the process)."""
    (tmp_path / LOCK_FILE).write_text(
        json.dumps({"pid": 999999, "host": "gone", "command": "watch", "since": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"), WriterLock(tmp_path, "ingest"):
        holder = read_holder(tmp_path / LOCK_FILE)
        assert holder is not None
        assert holder.command == "ingest"
    assert "stale" in caplog.text


def test_lock_held_by_another_process_blocks_this_one(tmp_path: Path) -> None:
    code = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from il2ks.ingest.lock import WriterLock
        with WriterLock(Path({str(tmp_path)!r}), "other"):
            print("locked", flush=True)
            sys.stdin.readline()
        """
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(LockBusyError) as info, WriterLock(tmp_path, "ingest"):
            pass
        assert "other" in str(info.value)
        assert info.value.holder is not None
        assert info.value.holder.pid != os.getpid()  # (child.pid may be the venv launcher, not the Python process)
    finally:
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.close()
        child.wait(timeout=30)
    with WriterLock(tmp_path, "ingest"):
        pass


def test_killed_holder_does_not_leave_the_lock_stuck(tmp_path: Path) -> None:
    code = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from il2ks.ingest.lock import WriterLock
        lock = WriterLock(Path({str(tmp_path)!r}), "doomed")
        lock.acquire()
        print("locked", flush=True)
        time.sleep(60)
        """
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "locked"
    child.kill()
    child.wait(timeout=30)
    with WriterLock(tmp_path, "ingest", wait=10, poll_s=0.1):
        pass
