"""`[web] workers` above 1 in real processes: the workers start, share the load and agree, and a stop ends them all.

What must hold between workers is what a visitor can see: one ETag for one page, and a data change reaching every
worker. A stop goes the way of `il2ks run`, through `SubprocessChild`."""

import http.client
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from il2ks.serving.supervisor import ChildSpec, SubprocessChild
from tests.integration.test_ops_real_process import _free_port, il2ks  # pyright: ignore[reportPrivateUsage]

START_WAIT_S = 120
STOP_WAIT_S = 45
QUICK_STOP_S = 15  # well below the 30 seconds hypercorn gives requests in flight
REQUESTS = 40
HTTPS_FROM_THE_PROXY = {"X-Forwarded-Proto": "https"}  # `--https external`: the proxy says how the visitor came in
STARTED = re.compile(r"web worker (\d+) started")


class Web:
    def __init__(self, child: SubprocessChild, port: int, data: Path) -> None:
        self.child = child
        self.port = port
        self.data = data

    def get(self, path: str, **headers: str) -> tuple[int, str]:
        """(status, ETag), status 0 when nothing answers. Each call is a new connection, so workers take turns."""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers={**HTTPS_FROM_THE_PROXY, **headers}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
                return int(response.status), str(response.headers.get("ETag", ""))
        except urllib.error.HTTPError as error:
            return int(error.code), str(error.headers.get("ETag", ""))
        except (urllib.error.URLError, OSError):
            return 0, ""

    def worker_pids(self) -> set[str]:
        lines = [path.read_text(encoding="utf-8") for path in (self.data / "logs").glob("*.log*")]
        return set(STARTED.findall("\n".join(lines)))

    def wait_until_up(self, workers: int) -> None:
        deadline = time.monotonic() + START_WAIT_S
        while time.monotonic() < deadline:
            assert self.child.poll() is None, f"web exited early with code {self.child.poll()}"
            if self.get("/healthz")[0] == 200 and len(self.worker_pids()) >= workers:
                return
            time.sleep(0.3)
        pytest.fail(f"web did not start {workers} worker(s): {self.worker_pids()}")

    def many(self, path: str, **headers: str) -> list[tuple[int, str]]:
        with ThreadPoolExecutor(8) as pool:
            answers = [pool.submit(self.get, path, **headers) for _ in range(REQUESTS)]
            return [answer.result() for answer in answers]

    def wait_for_exit(self) -> None:
        deadline = time.monotonic() + STOP_WAIT_S
        while self.child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)


def _start(tmp_path: Path, workers: int) -> Iterator[Web]:
    data, logs = tmp_path / "data", tmp_path / "game-logs"
    logs.mkdir()
    il2ks(
        tmp_path, "setup", "--non-interactive", "--data-dir", str(data), "--logs-dir", str(logs),
        "--timezone", "UTC", "--admin-username", "boss", "--https", "external",
    )  # fmt: skip
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("IL2KS_")}
    env.update(PYTHONIOENCODING="utf-8", IL2KS_NO_POLLER="1", IL2KS_WEB_PORT=str(port), IL2KS_WEB_WORKERS=str(workers))
    child = SubprocessChild(
        ChildSpec("web", [sys.executable, "-m", "il2ks", "web", "--host", "127.0.0.1"], env=env, cwd=tmp_path)
    )
    web = Web(child, port, data)
    try:
        web.wait_until_up(workers)
        yield web
    finally:
        if child.poll() is None:
            child.kill()


@pytest.fixture
def two_workers(tmp_path: Path) -> Iterator[Web]:
    yield from _start(tmp_path, 2)


def test_two_workers_start_and_answer_side_by_side(two_workers: Web) -> None:
    assert len(two_workers.worker_pids()) == 2
    answers = two_workers.many("/")
    assert {status for status, _ in answers} == {200}
    assert len({etag for _, etag in answers}) == 1  # the boot id is shared: no worker invalidates another's page
    etag = answers[0][1]
    assert {status for status, _ in two_workers.many("/", **{"If-None-Match": etag})} == {304}


def test_a_data_change_reaches_every_worker(two_workers: Web) -> None:
    before = two_workers.get("/")[1]
    with sqlite3.connect(two_workers.data / "il2ks.sqlite3") as database:
        database.execute(  # a fresh install has no row yet
            "insert into il2ks_db_dataversion (id, version, updated_at) values (1, 1, datetime('now')) "
            "on conflict (id) do update set version = version + 1"
        )

    answers = two_workers.many("/")

    assert {status for status, _ in answers} == {200}
    assert before not in {etag for _, etag in answers}


def test_a_dead_worker_ends_the_server_for_its_supervisor_to_restart(two_workers: Web) -> None:
    victim = sorted(two_workers.worker_pids())[0]
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", victim, "/F"], capture_output=True, check=True)
    else:
        os.kill(int(victim), signal.SIGKILL)

    two_workers.wait_for_exit()

    assert two_workers.child.poll() is not None, "a half-dead server must not look healthy"
    assert two_workers.get("/healthz")[0] == 0


@pytest.mark.parametrize("workers", [1, 2])
def test_a_stop_ends_every_worker_and_nothing_else(tmp_path: Path, workers: int) -> None:
    """Stopping the workers with a console event would also reach what shares the console: Caddy, `watch`, `run`."""
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        _stop_and_check(tmp_path, workers)
        assert bystander.poll() is None, "the stop reached a process that is not part of web"
    finally:
        bystander.kill()
        bystander.wait()


def _stop_and_check(tmp_path: Path, workers: int) -> None:
    for web in _start(tmp_path, workers):
        web.child.stop()
        web.wait_for_exit()
        assert web.child.poll() is not None, "web did not stop"
        assert web.get("/healthz")[0] == 0  # the port is closed: no worker lives on


def test_a_stop_does_not_wait_for_the_connections_the_proxy_keeps_open(two_workers: Web) -> None:
    """Caddy holds idle connections: waiting for them would make every restart take half a minute."""
    held = [http.client.HTTPConnection("127.0.0.1", two_workers.port, timeout=30) for _ in range(6)]
    try:
        for connection in held:  # enough for both workers to hold one
            connection.request("GET", "/healthz", headers=HTTPS_FROM_THE_PROXY)
            assert connection.getresponse().read()
        started = time.monotonic()
        two_workers.child.stop()
        two_workers.wait_for_exit()
        took = time.monotonic() - started
    finally:
        for connection in held:
            connection.close()

    assert two_workers.child.poll() is not None, "web did not stop"
    assert took < QUICK_STOP_S
