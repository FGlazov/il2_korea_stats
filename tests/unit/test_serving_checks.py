"""Doctor checks for serving (FR-OPS-1): secret key, domain, Caddy, ports, static files, external proxy."""

import dataclasses
import shutil
from pathlib import Path

import pytest

from il2ks.config import CertSource, Config, HttpsConfig, HttpsMode, WebConfig, load_config
from il2ks.ops import doctor, serving_checks
from il2ks.ops.doctor import Check, Finding, Level
from il2ks.serving import caddy, procutil
from il2ks.serving.secret import DEV_SECRET_KEY, ensure_secret_key


def make(
    tmp_path: Path,
    *,
    debug: bool = False,
    mode: HttpsMode = "caddy",
    domain: str = "",
    cert: CertSource = "auto",
    web: WebConfig | None = None,
    caddy_path: Path | None = None,
) -> Config:
    base = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)
    https = HttpsConfig(mode=mode, domain=domain, cert=cert, caddy_path=caddy_path)
    return dataclasses.replace(base, debug=debug, https=https, web=web or WebConfig())


def run(check: Check, cfg: Config) -> list[Finding]:
    return list(check(cfg))


@pytest.fixture
def no_caddy_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing(name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", nothing)


# --- secret key and debug ---------------------------------------------------------------------------------------------


def test_the_placeholder_key_in_the_config_is_an_error(tmp_path: Path) -> None:
    cfg = make(tmp_path, web=WebConfig(secret_key=DEV_SECRET_KEY))
    findings = run(serving_checks.secret_key, cfg)
    assert [f.level for f in findings] == [Level.ERROR]
    assert "development key" in findings[0].title


def test_a_short_configured_key_is_a_warning(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.secret_key, make(tmp_path, web=WebConfig(secret_key="short")))] == [
        Level.WARN
    ]


def test_a_long_configured_key_and_a_generated_key_are_fine(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.secret_key, make(tmp_path, web=WebConfig(secret_key="x" * 40)))] == [
        Level.OK
    ]
    cfg = make(tmp_path)
    assert "not created yet" in run(serving_checks.secret_key, cfg)[0].detail
    ensure_secret_key(cfg.data_dir)
    findings = run(serving_checks.secret_key, cfg)
    assert [f.level for f in findings] == [Level.OK]
    assert "secret_key.txt" in findings[0].detail


def test_debug_mode_is_flagged(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.debug_mode, make(tmp_path, debug=True))] == [Level.WARN]
    assert run(serving_checks.debug_mode, make(tmp_path)) == []


# --- domain -----------------------------------------------------------------------------------------------------------


def test_no_domain_in_caddy_mode_warns_about_the_test_certificate(tmp_path: Path) -> None:
    findings = run(serving_checks.domain, make(tmp_path))
    assert [f.level for f in findings] == [Level.WARN]
    assert "self-signed" in findings[0].title
    assert "[https] domain" in findings[0].fix


def test_no_domain_in_external_mode_warns_unless_hosts_are_listed(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.domain, make(tmp_path, mode="external"))] == [Level.WARN]
    listed = make(tmp_path, mode="external", web=WebConfig(allowed_hosts=("stats.example.com",)))
    assert run(serving_checks.domain, listed) == []


def test_a_real_domain_is_fine(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.domain, make(tmp_path, domain="stats.example.com"))] == [Level.OK]


def test_an_ip_address_is_explained(tmp_path: Path) -> None:
    findings = run(serving_checks.domain, make(tmp_path, domain="203.0.113.7"))
    assert [f.level for f in findings] == [Level.OK]
    assert "6 days" in findings[0].detail
    assert "Caddy 2.10" in findings[0].detail


@pytest.mark.parametrize("name", ["stats", "stats.lan", "pc.local", "localhost"])
def test_a_local_name_cannot_get_a_public_certificate(tmp_path: Path, name: str) -> None:
    assert [f.level for f in run(serving_checks.domain, make(tmp_path, domain=name))] == [Level.WARN]


def test_a_local_name_with_the_internal_certificate_is_the_admins_choice(tmp_path: Path) -> None:
    assert [f.level for f in run(serving_checks.domain, make(tmp_path, domain="stats.lan", cert="internal"))] == [
        Level.OK
    ]


# --- caddy ------------------------------------------------------------------------------------------------------------


def test_caddy_found(tmp_path: Path) -> None:
    binary = tmp_path / "caddy.exe"
    binary.write_bytes(b"")
    findings = run(serving_checks.caddy_binary, make(tmp_path, caddy_path=binary))
    assert [f.level for f in findings] == [Level.OK]
    assert str(binary) in findings[0].detail


def test_caddy_missing_is_an_error_with_install_advice(tmp_path: Path, no_caddy_on_path: None) -> None:
    findings = run(serving_checks.caddy_binary, make(tmp_path))
    assert [f.level for f in findings] == [Level.ERROR]
    assert "winget install CaddyServer.Caddy" in findings[0].fix
    assert "external" in findings[0].fix


def test_a_wrong_caddy_path_is_an_error_that_names_the_path(tmp_path: Path, no_caddy_on_path: None) -> None:
    findings = run(serving_checks.caddy_binary, make(tmp_path, caddy_path=tmp_path / "typo.exe"))
    assert [f.level for f in findings] == [Level.ERROR]
    assert "typo.exe" in findings[0].detail


def test_no_caddy_check_for_external_mode_or_debug(tmp_path: Path, no_caddy_on_path: None) -> None:
    assert run(serving_checks.caddy_binary, make(tmp_path, mode="external")) == []
    assert run(serving_checks.caddy_binary, make(tmp_path, debug=True)) == []


# --- ports ------------------------------------------------------------------------------------------------------------


def probe_returning(monkeypatch: pytest.MonkeyPatch, busy: set[int]) -> list[int]:
    asked: list[int] = []

    def probe(port: int) -> bool:
        asked.append(port)
        return port in busy

    monkeypatch.setattr(serving_checks, "port_probe", probe)
    return asked


def test_free_ports_are_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = probe_returning(monkeypatch, set())
    findings = run(serving_checks.ports, make(tmp_path))
    assert asked == [8000, 80, 443]
    assert [f.level for f in findings] == [Level.OK] * 3


def test_a_port_held_by_another_program_is_an_error_with_the_way_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_returning(monkeypatch, {443})
    findings = run(serving_checks.ports, make(tmp_path))
    errors = [f for f in findings if f.level is Level.ERROR]
    assert len(errors) == 1
    assert "Port 443" in errors[0].title
    assert 'mode = "external"' in errors[0].fix
    assert "netstat" in errors[0].fix


def test_the_web_port_clash_suggests_another_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_returning(monkeypatch, {8000})
    errors = [f for f in run(serving_checks.ports, make(tmp_path)) if f.level is Level.ERROR]
    assert "[web] port" in errors[0].fix


def test_ports_held_by_our_own_run_are_fine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_returning(monkeypatch, {8000, 80, 443})
    cfg = make(tmp_path)
    cfg.data_dir.mkdir(parents=True)
    with procutil.run_lock(cfg.data_dir):  # this test process counts as the running il2ks
        findings = run(serving_checks.ports, cfg)
    assert [f.level for f in findings] == [Level.OK] * 3
    assert "il2ks run" in findings[0].detail


def test_a_stale_run_json_does_not_make_foreign_ports_ours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_returning(monkeypatch, {8000})
    cfg = make(tmp_path)
    cfg.data_dir.mkdir(parents=True)
    procutil.write_run_state(cfg.data_dir, {"web": 1})  # a leftover from before a reboot: this PID is alive, no lock is
    errors = [f for f in run(serving_checks.ports, cfg) if f.level is Level.ERROR]
    assert len(errors) == 1


def test_external_mode_and_debug_only_check_the_web_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = probe_returning(monkeypatch, set())
    run(serving_checks.ports, make(tmp_path, mode="external"))
    run(serving_checks.ports, make(tmp_path, debug=True))
    assert asked == [8000, 8000]


def test_the_real_probe_sees_a_listening_socket() -> None:
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port: int = server.getsockname()[1]
        assert serving_checks.port_in_use(port)
    assert not serving_checks.port_in_use(port)


# --- static files and external proxy ---------------------------------------------------------------------------


def test_static_files_collected_or_not(tmp_path: Path) -> None:
    cfg = make(tmp_path)
    findings = run(serving_checks.static_files, cfg)
    assert [f.level for f in findings] == [Level.WARN]
    assert "il2ks web" in findings[0].fix
    manifest = cfg.data_dir / "staticfiles" / "staticfiles.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    assert [f.level for f in run(serving_checks.static_files, cfg)] == [Level.OK]
    assert run(serving_checks.static_files, make(tmp_path, debug=True)) == []


def test_external_mode_hints_and_the_listening_address(tmp_path: Path) -> None:
    assert run(serving_checks.external_proxy, make(tmp_path)) == []
    findings = run(serving_checks.external_proxy, make(tmp_path, mode="external"))
    assert [f.level for f in findings] == [Level.OK]
    assert "X-Forwarded-Proto" in findings[0].detail
    assert "docs/reverse-proxy.md" in findings[0].detail
    open_host = make(tmp_path, mode="external", web=WebConfig(host="0.0.0.0"))
    assert [f.level for f in run(serving_checks.external_proxy, open_host)] == [Level.OK, Level.WARN]


# --- registered with the doctor ---------------------------------------------------------------------------------------


def test_the_checks_run_through_the_doctor_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_caddy_on_path: None
) -> None:
    probe_returning(monkeypatch, set())
    assert "il2ks.ops.serving_checks" in doctor.CHECK_MODULES
    titles = [f.title for f in doctor.run_checks(make(tmp_path, domain="stats.example.com"))]
    assert "Secret key" in titles
    assert "Caddy (HTTPS proxy) is not installed" in titles
    assert caddy.find_caddy(make(tmp_path)) is None
