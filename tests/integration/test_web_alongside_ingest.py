"""`il2ks web` runs next to a long `ingest` / `watch` (FR-ING-20): web takes no writer lock just to look."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.integration.test_ops_real_process import _free_port, il2ks  # pyright: ignore[reportPrivateUsage]

HOLD_LOCK = (
    "import sys; from pathlib import Path; from il2ks.ingest.lock import WriterLock; "
    "lock = WriterLock(Path(sys.argv[1]), 'ingest'); lock.acquire(); print('held', flush=True); sys.stdin.readline(); "
    "lock.release()"
)


def _get_status(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return int(response.status)
    except (urllib.error.URLError, OSError):
        return None


def _wait_for_200(url: str, web: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        assert web.poll() is None, f"web exited early with code {web.returncode}"
        if _get_status(url) == 200:
            return
        time.sleep(0.3)
    pytest.fail(f"web did not answer 200 on {url}")


def _env(port: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("IL2KS_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["IL2KS_WEB_PORT"] = str(port)
    return env


def test_web_serves_while_the_writer_lock_is_held_and_ingest_finishes_beside_it(tmp_path: Path) -> None:
    data, logs = tmp_path / "data", tmp_path / "logs"
    logs.mkdir()
    il2ks(
        tmp_path, "setup", "--non-interactive", "--data-dir", str(data), "--logs-dir", str(logs),
        "--timezone", "UTC", "--admin-username", "boss", "--https", "external",
    )  # fmt: skip
    fixtures = tmp_path / "import"
    fixtures.mkdir()
    shutil.copy(
        REPO_ROOT / "tests" / "fixtures" / "logs" / "typical.txt.zip",
        fixtures / "missionReport(2025-01-01_12-00-00)[0].txt.zip",
    )

    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(data)],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    web: subprocess.Popen[str] | None = None
    try:
        assert holder.stdout is not None
        assert holder.stdin is not None
        assert holder.stdout.readline().strip() == "held"
        # A second writer is refused while the lock is held ...
        assert il2ks(tmp_path, "ingest", "--from", str(fixtures), check=False).returncode != 0
        # ... but web starts and answers: it needs no lock when no migration is pending.
        port = _free_port()
        web = subprocess.Popen(
            [sys.executable, "-m", "il2ks.cli", "web", "--dev", "--host", "127.0.0.1", "--port", str(port)],
            cwd=tmp_path,
            env=_env(port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        url = f"http://127.0.0.1:{port}/"
        _wait_for_200(url, web)
        # The long ingest ends; a real ingest then runs while web keeps serving.
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=30)
        assert il2ks(tmp_path, "ingest", "--from", str(fixtures)).returncode == 0
        assert web.poll() is None
        assert _get_status(url) == 200
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.wait()
        if web is not None:
            web.kill()
            web.wait()
