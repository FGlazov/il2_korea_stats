"""The `[web]` / `[https]` / `debug` configuration (TD-10, TD-23, NFR-SEC-2)."""

from pathlib import Path

import pytest

from il2ks.config import Config, ConfigError, HttpsConfig, WebConfig, load_config, normalize_domain


def cfg_from(tmp_path: Path, toml: str = "", env: dict[str, str] | None = None) -> Config:
    file = tmp_path / "il2ks.toml"
    file.write_text(toml, encoding="utf-8")
    return load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "data"), **(env or {})})


def test_defaults_are_localhost_8000_caddy_and_production(tmp_path: Path) -> None:
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")})
    assert cfg.web == WebConfig(host="127.0.0.1", port=8000, workers=1, threads=4, allowed_hosts=(), secret_key="")
    assert cfg.https == HttpsConfig(mode="caddy", domain="", email="", cert="auto", caddy_path=None)
    assert (cfg.https.http_port, cfg.https.https_port, cfg.https.hsts_seconds) == (80, 443, 86400)
    assert cfg.debug is False


def test_toml_values(tmp_path: Path) -> None:
    cfg = cfg_from(
        tmp_path,
        """
        debug = true
        [web]
        host = "0.0.0.0"
        port = 9000
        workers = 2
        threads = 8
        allowed_hosts = ["stats.example.org", ".example.net"]
        secret_key = "abc"
        [https]
        mode = "external"
        domain = "Stats.Example.COM"
        email = "me@example.com"
        cert = "internal"
        caddy_path = "bin/caddy.exe"
        http_port = 8080
        https_port = 8443
        hsts_seconds = 0
        """,
    )
    assert cfg.debug is True
    assert (cfg.web.host, cfg.web.port, cfg.web.workers, cfg.web.threads) == ("0.0.0.0", 9000, 2, 8)
    assert cfg.web.allowed_hosts == ("stats.example.org", ".example.net")
    assert cfg.web.secret_key == "abc"
    assert (cfg.https.mode, cfg.https.domain, cfg.https.email, cfg.https.cert) == (
        "external",
        "stats.example.com",
        "me@example.com",
        "internal",
    )
    assert cfg.https.caddy_path == tmp_path / "bin" / "caddy.exe"  # relative to the config file
    assert (cfg.https.http_port, cfg.https.https_port, cfg.https.hsts_seconds) == (8080, 8443, 0)


def test_env_overrides_and_comma_lists(tmp_path: Path) -> None:
    cfg = cfg_from(
        tmp_path,
        '[https]\ndomain = "from-file.example.com"',
        {
            "IL2KS_HTTPS_DOMAIN": "from-env.example.com",
            "IL2KS_WEB_PORT": "8123",
            "IL2KS_WEB_ALLOWED_HOSTS": "a.example.com, b.example.com",
            "IL2KS_DEBUG": "1",
        },
    )
    assert cfg.https.domain == "from-env.example.com"
    assert cfg.web.port == 8123
    assert cfg.web.allowed_hosts == ("a.example.com", "b.example.com")
    assert cfg.debug is True


def test_the_secret_key_never_appears_in_the_repr(tmp_path: Path) -> None:
    cfg = cfg_from(tmp_path, '[web]\nsecret_key = "super-secret-value"')
    assert "super-secret-value" not in repr(cfg)


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[web]\nport = 70000", "web.port"),
        ("[web]\nport = 0", "web.port"),
        ("[web]\nworkers = 0", "web.workers"),
        ('[web]\nhost = ""', "web.host"),
        ("[web]\nallowed_hosts = [1]", "web.allowed_hosts"),
        ("[web]\nlogin_attempts = 0", "web.login_attempts"),
        ("[web]\nlogin_lockout_minutes = 0", "web.login_lockout_minutes"),
        ('[outbound]\nallow_private = ["not-a-network"]', "outbound.allow_private"),
        ('[https]\nmode = "nginx"', "https.mode"),
        ('[https]\ncert = "self"', "https.cert"),
        ("[https]\nhttps_port = 99999", "https.https_port"),
        ("[https]\nhsts_seconds = -1", "https.hsts_seconds"),
        ('[https]\ndomain = "https://stats.example.com"', "https.domain"),
        ('[https]\ndomain = "stats.example.com:8443"', "https.domain"),
        ('[https]\ndomain = "evil.com { respond 200 }"', "https.domain"),
        ('[https]\ndomain = "a{b}.example.com"', "https.domain"),
        ('[https]\ndomain = "a\\"b.example.com"', "https.domain"),
        ('[https]\ndomain = "a#b.example.com"', "https.domain"),
        ('[https]\ndomain = "-bad.example.com"', "https.domain"),
        ('[https]\nemail = "me@example.com\\n}"', "https.email"),
        ('[https]\nemail = "a b@example.com"', "https.email"),
        ('[https]\nemail = "me@exa{mple}.com"', "https.email"),
        ('[https]\nemail = "\\"me@example.com"', "https.email"),
        ('[https]\nemail = "not-an-address"', "https.email"),
        ('debug = "maybe"', "debug"),
    ],
)
def test_invalid_values_name_the_setting(tmp_path: Path, toml: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        cfg_from(tmp_path, toml)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("  Stats.Example.com  ", "stats.example.com"),
        ("203.0.113.7", "203.0.113.7"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
    ],
)
def test_normalize_domain(text: str, expected: str) -> None:
    assert normalize_domain(text) == expected


def test_login_lockout_and_outbound_settings(tmp_path: Path) -> None:
    """NFR-SEC-8 / NFR-SEC-9: the defaults, the file and the environment."""
    defaults = cfg_from(tmp_path)
    assert (defaults.web.login_attempts, defaults.web.login_lockout_minutes) == (5, 15)
    assert defaults.outbound.allow_private == ()

    cfg = cfg_from(
        tmp_path,
        '[web]\nlogin_attempts = 3\n[outbound]\nallow_private = ["192.168.1.0/24"]',
        {"IL2KS_WEB_LOGIN_LOCKOUT_MINUTES": "30"},
    )
    assert (cfg.web.login_attempts, cfg.web.login_lockout_minutes) == (3, 30)
    assert cfg.outbound.allow_private == ("192.168.1.0/24",)
