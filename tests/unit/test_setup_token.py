"""The first-run setup page's gate (serving.setup_token): token file, "straight from this machine", guess limiter."""

import os
from pathlib import Path

import pytest

from il2ks.serving import setup_token
from il2ks.serving.setup_token import AttemptLimiter, host_name, is_direct_local_request, setup_url


def meta(remote: str = "127.0.0.1", host: str = "localhost:8000", **extra: str) -> dict[str, object]:
    return {"REMOTE_ADDR": remote, "HTTP_HOST": host, **extra}


@pytest.mark.parametrize("remote", ["127.0.0.1", "127.5.5.5", "::1", "::ffff:127.0.0.1"])
def test_loopback_peers_are_local(remote: str) -> None:
    assert is_direct_local_request(meta(remote=remote))


@pytest.mark.parametrize(
    "remote", ["192.168.1.20", "10.0.0.5", "203.0.113.9", "::ffff:10.0.0.5", "2001:db8::1", "", "nonsense"]
)
def test_other_peers_are_not_local(remote: str) -> None:
    assert not is_direct_local_request(meta(remote=remote))


@pytest.mark.parametrize("header", setup_token.FORWARDING_HEADERS)
def test_any_proxy_header_means_not_direct(header: str) -> None:
    """Caddy (and nginx, IIS) connect from 127.0.0.1 and add these: the public site can never reach the page."""
    assert not is_direct_local_request(meta(**{header: "203.0.113.9"}))


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST"])
def test_local_host_names_pass(host: str) -> None:
    assert is_direct_local_request(meta(host=host))


@pytest.mark.parametrize(
    "host", ["evil.example.com", "evil.example.com:8000", "stats.example.com", "", "localhost.evil.com"]
)
def test_other_host_names_are_refused_against_dns_rebinding(host: str) -> None:
    assert not is_direct_local_request(meta(host=host))


def test_host_name_strips_port_and_brackets() -> None:
    assert host_name("Example.com:443") == "example.com"
    assert host_name("[::1]:8000") == "::1"
    assert host_name("::1") == "::1"


def test_ensure_token_creates_a_private_file_once_and_keeps_it(tmp_path: Path) -> None:
    first = setup_token.ensure_token(tmp_path / "data")  # also creates the folder
    assert len(first) >= 30
    assert setup_token.read_token(tmp_path / "data") == first
    assert (
        setup_token.ensure_token(tmp_path / "data") == first
    )  # a restart does not invalidate an address printed earlier
    if os.name != "nt":
        assert (tmp_path / "data" / setup_token.TOKEN_FILE).stat().st_mode & 0o077 == 0


def test_token_matching_and_discarding(tmp_path: Path) -> None:
    assert not setup_token.token_matches(tmp_path, "anything")  # nothing pending: nothing matches, not even ""
    assert not setup_token.token_matches(tmp_path, "")
    token = setup_token.ensure_token(tmp_path)
    assert setup_token.token_matches(tmp_path, token)
    assert setup_token.token_matches(tmp_path, f"  {token}\n")  # pasted with spaces
    assert not setup_token.token_matches(tmp_path, token[:-1])
    assert not setup_token.token_matches(tmp_path, token + "x")
    setup_token.discard_token(tmp_path)
    assert setup_token.read_token(tmp_path) == ""
    assert not setup_token.token_matches(tmp_path, token)
    setup_token.discard_token(tmp_path)  # idempotent


def test_a_fresh_token_after_discarding_is_a_different_one(tmp_path: Path) -> None:
    old = setup_token.ensure_token(tmp_path)
    setup_token.discard_token(tmp_path)
    assert setup_token.ensure_token(tmp_path) != old


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:8000/setup/?token=T"),
        ("0.0.0.0", "http://localhost:8000/setup/?token=T"),
        ("::", "http://localhost:8000/setup/?token=T"),
        ("::1", "http://[::1]:8000/setup/?token=T"),
        ("localhost", "http://localhost:8000/setup/?token=T"),
    ],
)
def test_setup_url(host: str, expected: str) -> None:
    assert setup_url(host, 8000, "T") == expected


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_limiter_locks_after_the_allowed_failures_and_unlocks_later() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_failures=3, lockout_s=60.0, clock=clock)
    for _ in range(2):
        limiter.record_failure()
        assert not limiter.locked()
    limiter.record_failure()
    assert limiter.locked()
    clock.now += 59
    assert limiter.locked()
    clock.now += 2
    assert not limiter.locked()
    limiter.record_failure()  # the count started over
    assert not limiter.locked()


def test_limiter_reset_after_success() -> None:
    limiter = AttemptLimiter(max_failures=2, lockout_s=60.0, clock=FakeClock())
    limiter.record_failure()
    limiter.reset()
    limiter.record_failure()
    assert not limiter.locked()
