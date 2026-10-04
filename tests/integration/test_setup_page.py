"""The first-run setup page (doc 07 option B): who can reach it, what it validates, what it writes, and that it is gone
afterwards. Security first: every test of "refused" checks that nothing was created."""

import os
import re
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from pytest_django.fixtures import Settings

from il2ks.config import load_config
from il2ks.ops import admin, serving_checks
from il2ks.ops.detect import LogFolder
from il2ks.ops.setup import SetupAnswers, SetupOptions, WebSetupResult, complete_web_setup, run_setup
from il2ks.ops.template import toml_string
from il2ks.serving import procutil, setup_token
from il2ks.web.views import setup as setup_view
from tests.ops_helpers import ScriptedPrompter, returning

pytestmark = pytest.mark.django_db

PASSWORD = "Tr1cky-Horse-Battery-9"
URL = "/setup/"


@pytest.fixture
def pending(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh install waiting for setup: a data folder with a token, a clean IL2KS_* environment, no config file."""
    for name in list(os.environ):
        if name.startswith("IL2KS_") and name != "IL2KS_TEST_DB":
            monkeypatch.delenv(name)
    data = tmp_path / "data"
    monkeypatch.setenv("IL2KS_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)
    settings.DATA_DIR = data
    setup_token.ensure_token(data)
    monkeypatch.setattr(setup_view, "candidate_folders", returning(list[LogFolder]()))
    setup_view.limiter.reset()
    return data


@pytest.fixture
def token(pending: Path) -> str:
    return setup_token.read_token(pending)


def local(*, csrf: bool = False) -> Client:
    """A browser on the server machine, opening http://localhost:8000/ (what the setup page is meant for)."""
    return Client(HTTP_HOST="localhost:8000", REMOTE_ADDR="127.0.0.1", enforce_csrf_checks=csrf)


def form(**overrides: str) -> dict[str, str]:
    data = {
        "logs_choice": "",
        "logs_custom": "",
        "timezone": "Asia/Seoul",
        "https_mode": "caddy",
        "domain": "",
        "email": "",
        "admin_username": "boss",
        "admin_password": PASSWORD,
        "admin_password2": PASSWORD,
    }
    data.update(overrides)
    return data


def submit(client: Client, token: str, **overrides: str) -> str:
    response = client.post(URL, {**form(**overrides), "token": token})
    return response.content.decode()


def target(pending: Path) -> Path:
    return pending / "il2ks.toml"  # no config existed: the page writes into the data folder


def no_admin() -> bool:
    return not get_user_model().objects.filter(is_superuser=True).exists()


# --- who can reach it ---------------------------------------------------------------------------------------------


def test_the_form_opens_locally_with_the_token(pending: Path, token: str) -> None:
    response = local().get(URL, {"token": token})
    assert response.status_code == 200
    html = response.content.decode()
    assert 'name="admin_password"' in html
    assert f'value="{token}"' in html  # carried through the form, so no cookie or session is needed
    assert "Cache-Control" in response.headers
    assert "no-store" in response.headers["Cache-Control"]


def test_the_page_is_a_404_when_setup_is_not_pending(pending: Path, token: str) -> None:
    setup_token.discard_token(pending)
    assert local().get(URL, {"token": token}).status_code == 404
    assert local().post(URL, {**form(), "token": token}).status_code == 404
    assert no_admin()
    assert not target(pending).exists()


@pytest.mark.parametrize("remote", ["192.168.1.20", "203.0.113.9", "10.1.2.3"])
def test_other_machines_get_a_404_even_with_the_token(pending: Path, token: str, remote: str) -> None:
    client = Client(HTTP_HOST="localhost:8000", REMOTE_ADDR=remote)
    assert client.get(URL, {"token": token}).status_code == 404
    assert client.post(URL, {**form(), "token": token}).status_code == 404
    assert no_admin()


@pytest.mark.parametrize(
    "header",
    ["HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_PROTO", "HTTP_X_REAL_IP", "HTTP_FORWARDED", "HTTP_X_FORWARDED_HOST"],
)
def test_requests_through_the_reverse_proxy_get_a_404(pending: Path, token: str, header: str) -> None:
    """Caddy and every other proxy connect from 127.0.0.1 but add forwarding headers: the public site never gets in."""
    extra: dict[str, Any] = {header: "203.0.113.9"}
    assert local().get(URL, {"token": token}, **extra).status_code == 404
    assert no_admin()


def test_a_foreign_host_name_gets_a_404_against_dns_rebinding(pending: Path, token: str) -> None:
    client = Client(HTTP_HOST="evil.example.com", REMOTE_ADDR="127.0.0.1")
    assert client.get(URL, {"token": token}).status_code == 404


def test_no_token_shows_where_to_find_it_and_never_the_form(pending: Path, token: str) -> None:
    response = local().get(URL)
    assert response.status_code == 403
    html = response.content.decode()
    assert "admin_password" not in html
    assert token not in html  # the token itself is never shown
    assert str(setup_token.token_path(pending)) in html


def test_a_wrong_token_is_refused(pending: Path, token: str) -> None:
    response = local().get(URL, {"token": token[:-1] + ("A" if token[-1] != "A" else "B")})
    assert response.status_code == 403
    assert "admin_password" not in response.content.decode()
    assert local().post(URL, {**form(), "token": "wrong"}).status_code == 403
    assert no_admin()
    assert not target(pending).exists()


def test_guessing_is_locked_out_even_for_the_right_token_afterwards(pending: Path, token: str) -> None:
    for _ in range(setup_token.MAX_FAILURES):
        assert local().get(URL, {"token": "guess"}).status_code == 403
    response = local().get(URL, {"token": token})
    assert response.status_code == 429
    assert "admin_password" not in response.content.decode()


def test_a_right_token_resets_the_count(pending: Path, token: str) -> None:
    for _ in range(setup_token.MAX_FAILURES - 1):
        local().get(URL, {"token": "guess"})
    assert local().get(URL, {"token": token}).status_code == 200
    for _ in range(setup_token.MAX_FAILURES - 1):
        local().get(URL, {"token": "guess"})
    assert local().get(URL, {"token": token}).status_code == 200


def test_other_methods_are_refused_after_the_gate(pending: Path, token: str) -> None:
    assert local().put(URL).status_code == 405
    assert Client(HTTP_HOST="localhost", REMOTE_ADDR="8.8.8.8").put(URL).status_code == 404


def test_an_admin_created_another_way_ends_setup_and_removes_the_token(pending: Path, token: str) -> None:
    get_user_model().objects.create_superuser("someone", "", "x")
    assert local().get(URL, {"token": token}).status_code == 404
    assert setup_token.read_token(pending) == ""  # the stale token is cleaned up on the spot


# --- CSRF ---------------------------------------------------------------------------------------------------------


def test_a_post_without_the_csrf_token_is_rejected(pending: Path, token: str) -> None:
    client = local(csrf=True)
    response = client.post(URL, {**form(), "token": token})
    assert response.status_code == 403
    assert no_admin()
    assert not target(pending).exists()


def test_a_post_with_the_csrf_token_goes_through(pending: Path, token: str) -> None:
    client = local(csrf=True)
    html = client.get(URL, {"token": token}).content.decode()
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    assert csrf is not None
    response = client.post(URL, {**form(), "token": token, "csrfmiddlewaretoken": csrf.group(1)})
    assert response.status_code == 200
    assert not no_admin()


def test_the_csrf_check_does_not_hide_the_404_after_completion(pending: Path, token: str) -> None:
    setup_token.discard_token(pending)
    client = local(csrf=True)
    assert client.post(URL, {**form(), "token": token}).status_code == 404


# --- validation ---------------------------------------------------------------------------------------------------


def test_nothing_is_written_when_the_form_is_invalid(pending: Path, token: str) -> None:
    html = submit(
        local(),
        token,
        admin_password="short",
        admin_password2="short",
        domain="https://x.example.com:8443/a b",
        timezone="Mars/Base",
    )
    assert "Not saved yet" in html
    assert no_admin()
    assert not target(pending).exists()
    assert setup_token.read_token(pending) == token  # still pending: the admin can try again


def test_the_password_goes_through_djangos_validators(pending: Path, token: str) -> None:
    for weak in ("12345678", "password"):
        html = submit(local(), token, admin_password=weak, admin_password2=weak)
        assert "field-error" in html
    assert no_admin()


def test_the_two_passwords_must_match(pending: Path, token: str) -> None:
    html = submit(local(), token, admin_password2=PASSWORD + "x")
    assert "The two passwords differ" in html
    assert no_admin()


def test_the_password_is_never_sent_back(pending: Path, token: str) -> None:
    html = submit(local(), token, admin_password2="nope")
    assert PASSWORD not in html


def test_a_bad_domain_is_explained(pending: Path, token: str) -> None:
    html = submit(local(), token, domain="stats.example.com:8443")
    assert "stats.example.com:8443" in html or "no port" in html
    assert no_admin()


@pytest.mark.parametrize("email", ["not-an-address", "a@b", 'x"y@example.com', "a@example.com b@example.com"])
def test_a_bad_email_is_refused_by_the_same_check_as_il2ks_setup(pending: Path, token: str, email: str) -> None:
    html = submit(local(), token, email=email, domain="stats.example.com")
    assert "not an e-mail address" in html
    assert no_admin()
    assert not target(pending).exists()


def test_the_finishing_marker_is_there_while_the_answer_is_made_and_gone_afterwards(
    pending: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`il2ks run` must not restart the stack for the new config until the summary page has been rendered."""
    seen: list[bool] = []
    original = setup_view.complete_web_setup

    def spy(
        answers: SetupAnswers, path: Path, env: Mapping[str, str], *, admin_username: str, admin_password: str
    ) -> WebSetupResult:
        seen.append(setup_token.finishing(pending))
        return original(answers, path, env, admin_username=admin_username, admin_password=admin_password)

    monkeypatch.setattr(setup_view, "complete_web_setup", spy)
    assert "Setup finished" in submit(local(), token)
    assert seen == [True]
    assert not setup_token.finishing(pending)  # cleared again, also when the submit is refused:
    submit(local(), token, admin_password2="nope")
    assert not setup_token.finishing(pending)


def test_an_unknown_time_zone_is_refused(pending: Path, token: str) -> None:
    html = submit(local(), token, timezone="Not/AZone")
    assert "field-error" in html
    assert no_admin()


def test_a_missing_log_folder_is_refused_unless_confirmed(pending: Path, token: str, tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"
    html = submit(local(), token, logs_custom=str(missing))
    assert "does not exist" in html
    assert no_admin()
    html = submit(local(), token, logs_custom=str(missing), logs_allow_missing="on")
    assert "Setup finished" in html
    assert load_config(target(pending), os.environ).logs.dir == missing.resolve()


def test_a_file_is_not_a_log_folder(pending: Path, token: str, tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("x", encoding="utf-8")
    assert "is a file" in submit(local(), token, logs_custom=str(file))
    assert no_admin()


def make_logs(path: Path, reports: int) -> Path:
    path.mkdir(parents=True)
    for n in range(reports):
        (path / f"missionReport(2026-10-01_12-00-00)[{n}].txt").write_text("", encoding="utf-8")
    return path


def test_the_folder_check_says_whether_mission_reports_were_found(pending: Path, token: str, tmp_path: Path) -> None:
    with_reports = make_logs(tmp_path / "with", 3)
    html = submit(local(), token, logs_custom=str(with_reports), admin_password2="differs")  # re-shown, not saved
    assert "Found 3 mission report files" in html
    empty = make_logs(tmp_path / "empty", 0)
    html = submit(local(), token, logs_custom=str(empty), admin_password2="differs")
    assert "no mission reports are in it yet" in html


def test_detected_folders_are_offered_and_the_newest_is_preselected(
    pending: Path, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = make_logs(tmp_path / "found", 2)
    monkeypatch.setattr(
        setup_view, "candidate_folders", lambda: [LogFolder(found, 2, datetime(2026, 10, 1, tzinfo=UTC).timestamp())]
    )
    html = local().get(URL, {"token": token}).content.decode()
    assert str(found) in html
    assert re.search(rf'value="{re.escape(str(found))}"\s+checked', html)
    chosen = submit(local(), token, logs_choice=str(found))
    assert "Setup finished" in chosen
    assert load_config(target(pending), os.environ).logs.dir == found.resolve()


def test_the_time_zone_select_lists_the_zones_with_the_current_one_first(pending: Path, token: str) -> None:
    html = local().get(URL, {"token": token}).content.decode()
    options = re.findall(r'<option value="([^"]+)"', html)
    assert "Asia/Seoul" in options
    assert "Europe/Berlin" in options
    assert len(options) > 300


# --- success ------------------------------------------------------------------------------------------------------


def test_a_successful_setup_writes_the_config_creates_the_admin_and_closes_the_page(
    pending: Path, token: str, tmp_path: Path
) -> None:
    logs = make_logs(tmp_path / "logs", 1)
    client = local()
    html = submit(
        client,
        token,
        logs_custom=str(logs),
        domain="https://Stats.Example.com/",
        email="me@example.com",
        https_mode="caddy",
    )
    assert "Setup finished" in html
    assert "stats.example.com" in html
    assert PASSWORD not in html

    raw = tomllib.loads(target(pending).read_text(encoding="utf-8"))
    assert raw["data_dir"] == str(pending)
    assert raw["logs"] == {"dir": str(logs.resolve())}
    assert raw["server"]["timezone"] == "Asia/Seoul"
    assert raw["server"]["uid"] == (pending / "server_uid.txt").read_text(encoding="utf-8").strip()
    assert raw["https"] == {"mode": "caddy", "domain": "stats.example.com", "email": "me@example.com"}
    user = get_user_model().objects.get(username="boss")
    assert user.is_superuser
    assert user.is_staff
    assert user.check_password(PASSWORD)

    # Done for good: the token is gone, the same URL and a replay of the submit are 404.
    assert setup_token.read_token(pending) == ""
    assert client.get(URL, {"token": token}).status_code == 404
    assert client.post(URL, {**form(), "token": token}).status_code == 404
    assert get_user_model().objects.count() == 1


def test_the_summary_says_whether_il2ks_run_restarts_itself(
    pending: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = submit(local(), token)
    assert "Restart il2ks" in html
    assert "il2ks run" in html

    # Same again with a supervising `il2ks run` alive for this data dir.
    setup_token.ensure_token(pending)
    get_user_model().objects.all().delete()
    lock = procutil.run_lock(pending)  # what a live `il2ks run` holds (an OS lock, not a state file)
    lock.acquire()
    try:
        html = submit(local(), setup_token.read_token(pending))
    finally:
        lock.release()
    assert "restarts everything in a few seconds" in html
    assert "Restart il2ks" not in html


def test_the_summary_shows_the_doctors_findings(pending: Path, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serving_checks, "port_probe", returning(True))  # every port looks taken
    html = submit(local(), token)
    assert "Health check" in html
    assert "setup-finding--" in html
    assert "Port 443 (Caddy (https)) is already in use" in html
    assert "Port 8000 (the web server)" not in html  # our own port is not a conflict


def test_an_existing_config_is_kept_apart_from_the_answers_and_backed_up(
    pending: Path, token: str, tmp_path: Path
) -> None:
    config = tmp_path / "il2ks.toml"
    config.write_text(
        f'# mine\ndata_dir = "{pending.as_posix()}"\n[ingest]\nidle_minutes = 7\n[https]\ndomain = "old.example.com"\n',
        encoding="utf-8",
    )
    html = submit(local(), token, domain="new.example.com")
    assert "Setup finished" in html
    raw = tomllib.loads(config.read_text(encoding="utf-8"))
    assert raw["ingest"] == {"idle_minutes": 7}  # what the admin added by hand survives
    assert raw["https"]["domain"] == "new.example.com"
    assert raw["server"]["timezone"] == "Asia/Seoul"
    assert "# mine" in config.read_text(encoding="utf-8")
    backups = list(tmp_path.glob("il2ks.toml.bak-*"))
    assert len(backups) == 1
    assert "old.example.com" in backups[0].read_text(encoding="utf-8")


def test_clearing_the_domain_removes_it_from_an_existing_config(pending: Path, token: str, tmp_path: Path) -> None:
    config = tmp_path / "il2ks.toml"
    config.write_text(f'data_dir = "{pending.as_posix()}"\n[https]\ndomain = "old.example.com"\n', encoding="utf-8")
    submit(local(), token, domain="")
    assert "domain" not in tomllib.loads(config.read_text(encoding="utf-8")).get("https", {})


def test_a_rejected_password_at_the_last_moment_leaves_the_config_alone(
    pending: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form checks the password, but `complete_web_setup` checks again before writing anything."""
    answers = SetupAnswers(data_dir=pending, timezone="UTC", https_mode="caddy")
    with pytest.raises(admin.AdminError):
        complete_web_setup(answers, target(pending), os.environ, admin_username="boss", admin_password="123")
    assert not target(pending).exists()
    assert no_admin()


# --- parity with the command --------------------------------------------------------------------------------------


def normalized(text: str, data: Path, logs: Path) -> str:
    text = text.replace(toml_string(data), '"<DATA>"').replace(toml_string(logs), '"<LOGS>"')
    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UID>", text)


def test_the_page_writes_the_same_config_as_il2ks_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: Path
) -> None:
    for name in list(os.environ):
        if name.startswith("IL2KS_") and name != "IL2KS_TEST_DB":
            monkeypatch.delenv(name)
    cli_root, web_root = tmp_path / "cli", tmp_path / "web"
    cli_logs, web_logs = make_logs(cli_root / "logs", 1), make_logs(web_root / "logs", 1)

    def kind(root: Path) -> dict[str, str]:
        return {"IL2KS_DATA_DIR": str(root / "data")}

    options = SetupOptions(
        config_path=cli_root / "il2ks.toml",
        data_dir=cli_root / "data",
        logs_dir=cli_logs,
        timezone="Asia/Seoul",
        domain="Stats.Example.com",
        https_mode="caddy",
        email="me@example.com",
        admin_username="boss",
        admin_password=PASSWORD,
        non_interactive=True,
    )
    assert run_setup(options, ScriptedPrompter([]), env=kind(cli_root)) == 0

    answers = SetupAnswers(
        data_dir=web_root / "data",
        timezone="Asia/Seoul",
        https_mode="caddy",
        domain="stats.example.com",
        email="me@example.com",
        logs_dir=web_logs,
    )
    complete_web_setup(answers, web_root / "il2ks.toml", kind(web_root), admin_username="boss", admin_password=PASSWORD)

    cli_text = (cli_root / "il2ks.toml").read_text(encoding="utf-8")
    web_text = (web_root / "il2ks.toml").read_text(encoding="utf-8")
    assert normalized(web_text, web_root / "data", web_logs) == normalized(cli_text, cli_root / "data", cli_logs)
    assert get_user_model().objects.filter(username="boss", is_superuser=True).count() == 1


def test_finishing_il2ks_setup_in_the_terminal_also_closes_the_page(tmp_path: Path, pending: Path) -> None:
    """The token is only for the browser setup; any way of creating the admin ends it."""
    assert setup_token.read_token(pending)
    options = SetupOptions(
        config_path=tmp_path / "il2ks.toml",
        data_dir=pending,
        timezone="UTC",
        https_mode="external",
        admin_username="boss",
        admin_password=PASSWORD,
        non_interactive=True,
    )
    assert run_setup(options, ScriptedPrompter([]), env={"IL2KS_DATA_DIR": str(pending)}) == 0
    assert setup_token.read_token(pending) == ""


def test_the_csrf_cookie_of_the_setup_page_works_over_plain_http_even_in_production(
    pending: Path, token: str, settings: Settings
) -> None:
    """Production marks the cookie Secure, but this page is opened on http://localhost before HTTPS exists."""
    settings.CSRF_COOKIE_SECURE = True
    response = local().get(URL, {"token": token})
    assert response.cookies[settings.CSRF_COOKIE_NAME]["secure"] == ""
