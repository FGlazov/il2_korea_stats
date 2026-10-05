"""Browser tests (doc 08: Playwright end-to-end tests on key flows).

Opt-in: `IL2KS_TEST_E2E=1 uv run pytest -m e2e` (see `tests/conftest.py`; README "Development"). Needs Chromium:
`uv run playwright install chromium` (CI and fresh Linux machines: `... install --with-deps chromium`).

The server is the real application in a child process: `il2ks web --dev` (the production code path, with the HTTPS
redirect and cookie flags off, because the dev server speaks plain http), on a fresh data dir whose database is
filled by `tests.e2e.world`. Nothing here touches the developer's own data dir: every `IL2KS_*` variable is dropped
from the environment and replaced.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import ConsoleMessage, Page, expect

from tests.conftest import REPO_ROOT
from tests.e2e.world import World

SEED_TIMEOUT_S = 600
START_TIMEOUT_S = 180
EXPECT_TIMEOUT_MS = 15_000
"""Generous on purpose: assertions return the moment they hold, so a long limit costs nothing when the machine is
idle. Several `il2ks dev check --e2e` runs share one PC and a page that takes 0.3 s alone took 5+ s under load."""
NAVIGATION_TIMEOUT_MS = 30_000


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _child_env(data_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IL2KS_", "DJANGO_"))}
    env.update(
        IL2KS_DATA_DIR=str(data_dir),
        DJANGO_SETTINGS_MODULE="il2ks.settings",
        PYTHONPATH=str(REPO_ROOT),  # `tests.e2e.world` is imported by the seed script
        PYTHONIOENCODING="utf-8",
    )
    return env


def _wait_until_up(url: str, process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"the e2e server exited with code {process.returncode}:\n{log.read_text(encoding='utf-8')}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    process.terminate()
    pytest.fail(f"the e2e server did not answer on {url} in {START_TIMEOUT_S}s:\n{log.read_text(encoding='utf-8')}")


@pytest.fixture(scope="session")
def _e2e_data_dir(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, World]:
    """A fresh data dir holding the synthetic world (built once per test run)."""
    data_dir = tmp_path_factory.mktemp("e2e") / "data"
    data_dir.mkdir()
    seeded = subprocess.run(
        [sys.executable, "-m", "tests.e2e.world"],
        cwd=data_dir.parent,
        env=_child_env(data_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=SEED_TIMEOUT_S,
    )
    assert seeded.returncode == 0, f"seeding the e2e database failed:\n{seeded.stdout}\n{seeded.stderr}"
    return data_dir, World(**json.loads(seeded.stdout.strip().splitlines()[-1]))


@pytest.fixture(scope="session")
def world(_e2e_data_dir: tuple[Path, World]) -> World:
    """What the synthetic data holds (names, ids), for the assertions."""
    return _e2e_data_dir[1]


@pytest.fixture(scope="session")
def base_url(_e2e_data_dir: tuple[Path, World]) -> Iterator[str]:
    """Start `il2ks web --dev` on a free port; overrides pytest-base-url's fixture, so `page.goto("/")` works.

    Opt-in `IL2KS_E2E_BASE_URL=http://host:port` uses an already running server instead (faster local iteration). It
    must serve the seeded e2e world (`python -m tests.e2e.world` into its data dir); tests that write to the data dir
    (`set_branding`) change the local throwaway dir, not that server. Unset: the self-started server, as in CI."""
    external = os.environ.get("IL2KS_E2E_BASE_URL", "").strip().rstrip("/")
    if external:
        yield external
        return
    data_dir = _e2e_data_dir[0]
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log = data_dir.parent / "server.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [sys.executable, "-m", "il2ks.cli", "web", "--dev", "--host", "127.0.0.1", "--port", str(port)],
            cwd=data_dir.parent,
            env=_child_env(data_dir),
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_up(url + "/", process, log)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture
def set_branding(_e2e_data_dir: tuple[Path, World], base_url: str) -> Iterator[Callable[[dict[str, object]], None]]:
    """`set_branding({"links": [...], "theme": {...}})` changes the running site's branding (see `tests.e2e.branding`);
    the shipped look is restored after the test. `base_url` makes sure the server is up."""
    data_dir = _e2e_data_dir[0]

    def apply(spec: dict[str, object]) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "tests.e2e.branding", json.dumps(spec)],
            cwd=data_dir.parent,
            env=_child_env(data_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert done.returncode == 0, f"setting the branding failed:\n{done.stdout}\n{done.stderr}"

    try:
        yield apply
    finally:
        apply({})


TweakData = Callable[[str, int | None, dict[str, object]], None]


@pytest.fixture
def tweak_data(_e2e_data_dir: tuple[Path, World], base_url: str) -> Iterator[TweakData]:
    """`tweak_data("Mission", pk, {"is_live": True})` changes fields of one row of the running site (see
    `tests.e2e.tweaks`: `Mission`, `PlayerSortie`, or `SiteSettings` with no pk); the old values are restored after
    the test, last change first. `base_url` makes sure the server is up."""
    data_dir = _e2e_data_dir[0]
    undo: list[tuple[str, int | None, dict[str, object]]] = []

    def run(model: str, pk: int | None, fields: dict[str, object]) -> dict[str, object]:
        done = subprocess.run(
            [sys.executable, "-m", "tests.e2e.tweaks", json.dumps({"model": model, "pk": pk, "fields": fields})],
            cwd=data_dir.parent,
            env=_child_env(data_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert done.returncode == 0, f"tweaking {model} {pk} failed:\n{done.stdout}\n{done.stderr}"
        return cast("dict[str, object]", json.loads(done.stdout.strip().splitlines()[-1]))

    def apply(model: str, pk: int | None, fields: dict[str, object]) -> None:
        undo.append((model, pk, run(model, pk, fields)))

    try:
        yield apply
    finally:
        for model, pk, before in reversed(undo):
            run(model, pk, before)


@pytest.fixture(autouse=True)
def _default_timeouts(page: Page) -> None:
    """Playwright's own default is 30 s; a loaded machine needs more than the 5 s this used to set (see above)."""
    expect.set_options(timeout=EXPECT_TIMEOUT_MS)
    page.set_default_timeout(EXPECT_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)


@pytest.fixture(autouse=True)
def console_errors(page: Page, request: pytest.FixtureRequest) -> Iterator[list[str]]:
    """Every test fails when the browser logs an error or the page throws, unless it is marked `allow_console_errors`.

    A 404 page is an error to the browser's console on purpose (the failed request), hence the opt-out."""
    errors: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(f"console.error: {message.text} ({message.location.get('url', '')})")

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(f"uncaught: {exc}"))
    yield errors
    if "allow_console_errors" not in request.keywords:
        assert not errors, "the browser reported errors:\n" + "\n".join(errors)
