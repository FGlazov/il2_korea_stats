"""`il2ks doctor` in the web server (FR-OPS-1): a background thread runs the checks, the admin shows the result.

Alerts (every ERROR, the warnings marked urgent) get a red banner on every admin page; the admin home page lists every
warning. Staff only, never on public pages, and a request never runs a check."""

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client

from il2ks.config import Config, load_config
from il2ks.ops.doctor import Finding, Level
from il2ks.web import server_checks

pytestmark = pytest.mark.django_db

BANNER = "il2ks-override-warning"
ADMIN_PAGES = ["/admin/", "/admin/il2ks_db/player/", "/admin/ingestion/", "/admin/auth/user/"]

DISK = Finding(Level.ERROR, "Almost no disk space left", "0.1 GiB free", "Free up space now")
OLD_CADDY = Finding(Level.WARN, "Caddy 2.8.4 is out of date", "older than 2.11.9", "Update Caddy", urgent=True)
NO_BACKUP = Finding(Level.WARN, "No backup yet", "no il2ks backup", "Run il2ks backup.")
FINE = Finding(Level.OK, "Enough disk space", "50 GiB free")


@pytest.fixture(autouse=True)
def no_result() -> Iterator[None]:
    server_checks.reset()
    yield
    server_checks.reset()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)


def found(*findings: Finding) -> Callable[[Config], list[Finding]]:
    """A stand-in for the checks (`checks=`)."""

    def checks(_cfg: Config) -> list[Finding]:
        return list(findings)

    return checks


@pytest.fixture
def staff(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_alerts_show_as_a_banner_on_every_admin_page(staff: Client, cfg: Config, url: str) -> None:
    server_checks.run_once(cfg, checks=found(DISK, OLD_CADDY, NO_BACKUP, FINE))

    body = staff.get(url).content.decode()

    assert BANNER in body
    assert "Almost no disk space left" in body
    assert "Free up space now" in body
    assert "Caddy 2.8.4 is out of date" in body
    banner = body[body.index(BANNER) : body.index("</div>", body.index(BANNER))]
    assert "No backup yet" not in banner  # an ordinary warning is not an alert
    assert banner.index("Almost no disk space") < banner.index("Caddy 2.8.4")  # errors first


def test_the_home_page_lists_every_warning(staff: Client, cfg: Config) -> None:
    server_checks.run_once(cfg, checks=found(NO_BACKUP, FINE))

    body = staff.get("/admin/").content.decode()

    assert BANNER not in body  # a plain warning: no banner
    assert "Server checks" in body
    assert "No backup yet" in body
    assert "Run il2ks backup." in body
    assert "Enough disk space" not in body  # OK findings are not listed


def test_the_home_page_says_when_everything_is_fine(staff: Client, cfg: Config) -> None:
    server_checks.run_once(cfg, checks=found(FINE))
    assert "Everything is fine." in staff.get("/admin/").content.decode()


def test_nothing_is_shown_before_the_first_run(staff: Client) -> None:
    body = staff.get("/admin/").content.decode()
    assert BANNER not in body
    assert "Server checks" not in body


def test_the_login_and_public_pages_never_show_it(staff: Client, cfg: Config) -> None:
    server_checks.run_once(cfg, checks=found(DISK))
    assert "Almost no disk space" not in Client().get("/admin/login/").content.decode()
    for url in ("/", "/404-does-not-exist/"):
        assert "Almost no disk space" not in staff.get(url).content.decode()


def test_a_request_never_runs_a_check(staff: Client, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(_cfg: Config, *, skip: frozenset[str] = frozenset()) -> list[Finding]:
        raise AssertionError("a request ran the checks")

    monkeypatch.setattr(server_checks, "run_checks", must_not_run)
    assert staff.get("/admin/").status_code == 200


def test_the_server_skips_the_port_check(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """A running server holds its own ports: the check would call them taken by another program."""
    seen: list[frozenset[str]] = []

    def recording(_cfg: Config, *, skip: frozenset[str] = frozenset()) -> list[Finding]:
        seen.append(skip)
        return []

    monkeypatch.setattr(server_checks, "run_checks", recording)
    server_checks.run_once(cfg)
    assert seen == [frozenset({"ports"})]


def test_new_alerts_are_logged_once(cfg: Config, caplog: pytest.LogCaptureFixture) -> None:
    for _ in range(3):
        server_checks.run_once(cfg, checks=found(DISK, NO_BACKUP))
    logged = [r.getMessage() for r in caplog.records if r.name == "il2ks.web"]
    assert len([m for m in logged if "Almost no disk space" in m]) == 1
    assert not [m for m in logged if "No backup yet" in m]


def test_the_thread_runs_the_checks_and_survives_a_failing_run(cfg: Config) -> None:
    calls: list[int] = []

    def flaky(_cfg: Config) -> list[Finding]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database busy")
        return [DISK]

    thread = server_checks.start(cfg, first_delay_s=0.01, interval_s=0.01, checks=flaky)
    assert thread is not None
    stored = threading.Event()
    for _ in range(500):
        if server_checks.latest() is not None:
            stored.set()
            break
        stored.wait(0.01)
    result = server_checks.latest()
    assert result is not None
    assert result.alerts == [DISK]
    assert len(calls) >= 2
    assert server_checks.start(cfg, checks=flaky) is None  # once per process
    server_checks.reset()
    assert not thread.is_alive()
