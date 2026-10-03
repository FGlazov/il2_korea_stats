"""Django settings per mode (TD-11, TD-23, TD-28, NFR-SEC-2): the pure rules, and the settings module under test."""

import dataclasses
from pathlib import Path

from django.conf import settings

import il2ks.settings as project_settings
from il2ks.config import Config, HttpsConfig, WebConfig, load_config
from il2ks.serving import djsettings
from il2ks.serving.storage import LenientManifestStorage


def make(
    tmp_path: Path, *, debug: bool = False, domain: str = "", https_port: int = 443, hsts_seconds: int = 86400
) -> Config:
    base = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)
    https = HttpsConfig(domain=domain, https_port=https_port, hsts_seconds=hsts_seconds)
    return dataclasses.replace(base, debug=debug, https=https)


# --- hosts and origins ---------------------------------------------------------------------------------------------


def test_domain_becomes_the_allowed_host_plus_localhost(tmp_path: Path) -> None:
    hosts = djsettings.allowed_hosts(make(tmp_path, domain="stats.example.com"))
    assert hosts == ["stats.example.com", "localhost", "127.0.0.1", "[::1]"]
    assert "*" not in hosts


def test_extra_allowed_hosts_are_added(tmp_path: Path) -> None:
    cfg = make(tmp_path, domain="stats.example.com")
    cfg = dataclasses.replace(cfg, web=WebConfig(allowed_hosts=("alias.example.com",)))
    assert djsettings.allowed_hosts(cfg)[:2] == ["stats.example.com", "alias.example.com"]


def test_no_domain_in_production_accepts_any_host_behind_the_proxy(tmp_path: Path) -> None:
    assert "*" in djsettings.allowed_hosts(make(tmp_path))


def test_no_domain_in_debug_is_localhost_only(tmp_path: Path) -> None:
    assert djsettings.allowed_hosts(make(tmp_path, debug=True)) == ["localhost", "127.0.0.1", "[::1]"]


def test_an_ipv6_domain_is_bracketed(tmp_path: Path) -> None:
    assert djsettings.allowed_hosts(make(tmp_path, domain="2001:db8::1"))[0] == "[2001:db8::1]"
    assert djsettings.csrf_trusted_origins(make(tmp_path, domain="2001:db8::1")) == ["https://[2001:db8::1]"]


def test_csrf_origins_follow_the_domain_and_a_non_standard_port(tmp_path: Path) -> None:
    assert djsettings.csrf_trusted_origins(make(tmp_path, domain="stats.example.com")) == ["https://stats.example.com"]
    odd = make(tmp_path, domain="stats.example.com", https_port=8443)
    assert djsettings.csrf_trusted_origins(odd) == ["https://stats.example.com", "https://stats.example.com:8443"]
    assert djsettings.csrf_trusted_origins(make(tmp_path)) == []


def test_csrf_origins_for_extra_hosts_and_wildcards(tmp_path: Path) -> None:
    cfg = dataclasses.replace(make(tmp_path), web=WebConfig(allowed_hosts=(".example.net", "x.example.org", "*")))
    assert djsettings.csrf_trusted_origins(cfg) == ["https://*.example.net", "https://x.example.org"]


# --- HTTPS behaviour ----------------------------------------------------------------------------------------------


def test_production_is_https_only_with_modest_hsts_and_no_preload(tmp_path: Path) -> None:
    sec = djsettings.security_settings(make(tmp_path, domain="stats.example.com"))
    assert sec.proxy_ssl_header == ("HTTP_X_FORWARDED_PROTO", "https")
    assert sec.ssl_redirect is True
    assert sec.hsts_seconds == 86400  # one day to start
    assert sec.hsts_include_subdomains is False
    assert sec.hsts_preload is False
    assert sec.session_cookie_secure
    assert sec.csrf_cookie_secure
    assert sec.session_cookie_httponly
    assert sec.csrf_cookie_httponly
    assert sec.content_type_nosniff is True
    assert sec.referrer_policy == "same-origin"


def test_hsts_length_is_configurable_and_can_be_off(tmp_path: Path) -> None:
    assert djsettings.security_settings(make(tmp_path, hsts_seconds=31536000)).hsts_seconds == 31536000
    assert djsettings.security_settings(make(tmp_path, hsts_seconds=0)).hsts_seconds == 0


def test_debug_turns_the_https_parts_off_so_plain_http_localhost_works(tmp_path: Path) -> None:
    sec = djsettings.security_settings(make(tmp_path, debug=True))
    assert sec.proxy_ssl_header is None
    assert sec.ssl_redirect is False
    assert sec.hsts_seconds == 0
    assert not sec.session_cookie_secure
    assert not sec.csrf_cookie_secure
    # what needs no HTTPS stays on
    assert sec.content_type_nosniff
    assert sec.session_cookie_httponly
    assert sec.csrf_cookie_httponly


def test_static_files_are_hashed_in_production_and_plain_in_debug(tmp_path: Path) -> None:
    assert djsettings.staticfiles_backend(make(tmp_path)) == "il2ks.serving.storage.LenientManifestStorage"
    assert djsettings.staticfiles_backend(make(tmp_path, debug=True)) == djsettings.STATIC_BACKEND_DEV


def test_the_manifest_storage_does_not_crash_on_an_unknown_file() -> None:
    """A page that mentions a static file collectstatic never saw must still render (tests need no collectstatic)."""
    assert LenientManifestStorage.manifest_strict is False


# --- the settings module itself (pytest runs it with the defaults: no config file) ----------------------------------


def test_custom_folders_come_first_even_when_they_do_not_exist() -> None:
    custom = Path(settings.DATA_DIR) / "custom"
    assert settings.TEMPLATES[0]["DIRS"][0] == custom / "templates"
    assert settings.STATICFILES_DIRS[0] == custom / "static"


def test_static_root_is_in_the_data_dir_and_the_storage_is_set() -> None:
    assert Path(settings.STATIC_ROOT) == Path(settings.DATA_DIR) / "staticfiles"
    # The settings module itself (tests/conftest.py swaps the storage at runtime for template rendering).
    assert project_settings.STORAGES["staticfiles"]["BACKEND"] == djsettings.staticfiles_backend(
        load_config(create_server_uid=False)
    )


def test_staticfiles_w004_is_silenced_because_custom_static_may_not_exist() -> None:
    assert "staticfiles.W004" in settings.SILENCED_SYSTEM_CHECKS
