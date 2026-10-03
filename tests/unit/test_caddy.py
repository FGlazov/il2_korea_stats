"""Caddy (TD-23): the Caddyfile per mode, finding the binary, how it is started."""

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from il2ks.config import CertSource, Config, HttpsConfig, WebConfig, load_config
from il2ks.serving import caddy


def make(
    tmp_path: Path,
    *,
    domain: str = "",
    cert: CertSource = "auto",
    email: str = "",
    http_port: int = 80,
    https_port: int = 443,
    web: WebConfig | None = None,
    caddy_path: Path | None = None,
) -> Config:
    base = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)
    https = HttpsConfig(
        domain=domain, cert=cert, email=email, http_port=http_port, https_port=https_port, caddy_path=caddy_path
    )
    return dataclasses.replace(base, https=https, web=web or WebConfig())


# --- certificate plans ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "cert", "plan"),
    [
        ("stats.example.com", "auto", "public"),
        ("203.0.113.7", "auto", "public-ip"),
        ("2001:db8::1", "auto", "public-ip"),
        ("stats.example.com", "internal", "internal"),
        ("", "auto", "internal-no-domain"),
        ("", "internal", "internal-no-domain"),
    ],
)
def test_cert_plan(tmp_path: Path, domain: str, cert: CertSource, plan: str) -> None:
    assert caddy.cert_plan(make(tmp_path, domain=domain, cert=cert)) == plan


def test_only_the_test_plans_get_a_warning() -> None:
    assert "TESTING ONLY" in caddy.cert_plan_warning("internal-no-domain")
    assert "TESTING ONLY" in caddy.cert_plan_warning("internal")
    assert caddy.cert_plan_warning("public") == ""
    assert caddy.cert_plan_warning("public-ip") == ""


# --- Caddyfile ----------------------------------------------------------------------------------------------------


def test_domain_mode_is_automatic_https_with_a_reverse_proxy(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="stats.example.com", email="me@example.com"))
    assert "\nstats.example.com {\n" in text
    assert "\treverse_proxy 127.0.0.1:8000\n" in text
    assert "\tencode zstd gzip\n" in text
    assert "\temail me@example.com\n" in text
    assert "\tadmin off\n" in text
    assert "\thttp_port 80\n\thttps_port 443\n" in text
    assert "tls" not in text  # Caddy's defaults: public ACME, HTTP->HTTPS redirect
    assert "skip_install_trust" not in text
    assert "http://" not in text  # no hand-made redirect; Caddy's automatic one is right on 80/443


def test_no_email_line_without_an_email(tmp_path: Path) -> None:
    assert "	email " not in caddy.render_caddyfile(make(tmp_path, domain="stats.example.com"))


def test_access_log_goes_to_the_data_dir_logs_with_rolling(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="stats.example.com"))
    log_file = (tmp_path / "data" / "logs" / "caddy-access.log").as_posix()
    assert f'output file "{log_file}" {{' in text
    assert "roll_size 10MiB" in text
    assert "roll_keep_for 336h" in text  # log_keep_days (14) in hours


def test_ip_address_asks_for_the_short_lived_acme_profile(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="203.0.113.7"))
    assert "\n203.0.113.7 {\n\ttls {\n\t\tissuer acme {\n\t\t\tprofile shortlived\n" in text
    assert "skip_install_trust" not in text


def test_ipv6_address_is_bracketed_as_a_site_address(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="2001:db8::1"))
    assert "\n[2001:db8::1] {\n" in text
    assert "profile shortlived" in text


def test_internal_certificates_never_touch_the_trust_store(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="stats.example.com", cert="internal"))
    assert "\tskip_install_trust\n" in text
    assert "\n\ttls internal\n" in text


def test_no_domain_serves_any_name_from_the_local_ca_and_redirects_http(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path))
    assert "\n:443 {\n\ttls internal {\n\t\ton_demand\n\t}\n" in text
    assert "\treverse_proxy 127.0.0.1:8000\n" in text
    assert "\nhttp://:80 {\n\tredir https://{host}{uri} permanent\n}\n" in text
    assert "\tskip_install_trust\n" in text


def test_non_standard_ports_keep_the_port_in_the_redirect(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="localhost", cert="internal", http_port=8080, https_port=8443))
    assert "\thttp_port 8080\n\thttps_port 8443\n" in text
    assert "\nhttp://localhost:8080 {\n\tredir https://{host}:8443{uri} permanent\n}\n" in text
    nodomain = caddy.render_caddyfile(make(tmp_path, http_port=8080, https_port=8443))
    assert "\n:8443 {\n" in nodomain
    assert "\nhttp://:8080 {\n\tredir https://{host}:8443{uri} permanent\n}\n" in nodomain


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    [("127.0.0.1", 8000, "127.0.0.1:8000"), ("0.0.0.0", 9000, "127.0.0.1:9000"), ("::1", 8000, "[::1]:8000")],
)
def test_upstream_is_the_web_servers_address(tmp_path: Path, host: str, port: int, expected: str) -> None:
    cfg = make(tmp_path, domain="a.example.com", web=WebConfig(host=host, port=port))
    assert caddy.upstream(cfg) == expected
    assert f"reverse_proxy {expected}" in caddy.render_caddyfile(cfg)


def test_a_windows_path_is_written_with_forward_slashes_and_quotes(tmp_path: Path) -> None:
    text = caddy.render_caddyfile(make(tmp_path, domain="a.example.com"))
    assert "\\" not in text


def test_the_real_caddy_accepts_every_plan(tmp_path: Path) -> None:
    """Only when a Caddy binary is at hand: IL2KS_TEST_CADDY=<path>, or `caddy` on the PATH."""
    binary = os.environ.get("IL2KS_TEST_CADDY") or shutil.which("caddy")
    if not binary:
        pytest.skip("no Caddy binary (set IL2KS_TEST_CADDY)")
    cases: list[tuple[str, CertSource]] = [
        ("stats.example.com", "auto"),
        ("203.0.113.7", "auto"),
        ("2001:db8::1", "auto"),
        ("localhost", "internal"),
        ("", "auto"),
    ]
    for domain, cert in cases:
        cfg = make(tmp_path, domain=domain, cert=cert, https_port=8443, http_port=8080)
        path = caddy.write_caddyfile(cfg)
        result = subprocess.run(
            [binary, "validate", "--config", str(path), "--adapter", "caddyfile"],
            capture_output=True,
            text=True,
            check=False,
            env=caddy.caddy_environment(cfg),
        )
        assert result.returncode == 0, (domain, cert, result.stderr)


# --- the binary and how it is started ---------------------------------------------------------------------------


def test_find_caddy_prefers_config_then_path_then_data_dir_bin(tmp_path: Path) -> None:
    configured = tmp_path / "mine" / "caddy.exe"
    configured.parent.mkdir()
    configured.write_bytes(b"")
    bundled = tmp_path / "data" / "bin" / caddy.caddy_binary_name()
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    on_path = str(tmp_path / "elsewhere" / "caddy")

    assert caddy.find_caddy(make(tmp_path, caddy_path=configured), path_lookup=on_path) == configured
    assert caddy.find_caddy(make(tmp_path), path_lookup=on_path) == Path(on_path)
    assert caddy.find_caddy(make(tmp_path), path_lookup="") == bundled


def test_a_wrong_caddy_path_is_not_silently_replaced(tmp_path: Path) -> None:
    bundled = tmp_path / "data" / "bin" / caddy.caddy_binary_name()
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    assert caddy.find_caddy(make(tmp_path, caddy_path=tmp_path / "typo.exe"), path_lookup="/usr/bin/caddy") is None


def test_no_caddy_anywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing(name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", nothing)
    assert caddy.find_caddy(make(tmp_path)) is None


def test_caddy_keeps_its_state_in_the_data_dir(tmp_path: Path) -> None:
    cfg = make(tmp_path, domain="a.example.com")
    env = caddy.caddy_environment(cfg, {"PATH": "x"})
    assert env["XDG_DATA_HOME"] == str(tmp_path / "data" / "caddy" / "data")
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "data" / "caddy" / "config")
    assert env["PATH"] == "x"


def test_prepare_writes_the_caddyfile_and_builds_the_command(tmp_path: Path) -> None:
    cfg = make(tmp_path, domain="a.example.com")
    setup = caddy.prepare_caddy(cfg, Path("/opt/caddy"))
    path = tmp_path / "data" / "caddy" / "Caddyfile"
    assert path.read_text(encoding="utf-8") == caddy.render_caddyfile(cfg)
    assert setup.command == [str(Path("/opt/caddy")), "run", "--config", str(path), "--adapter", "caddyfile"]
    assert setup.plan == "public"
    assert (tmp_path / "data" / "logs").is_dir()
