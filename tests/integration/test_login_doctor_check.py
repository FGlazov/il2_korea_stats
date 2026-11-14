"""`il2ks doctor` lists the locked admin accounts and addresses (NFR-SEC-8)."""

from pathlib import Path
from uuid import uuid4

import pytest
from axes.models import AccessAttempt
from django.contrib.auth.models import User
from django.test import Client

from il2ks.config import Config
from il2ks.ops.doctor import CHECK_MODULES, Level
from il2ks.ops.login_checks import login_lockout_check

pytestmark = pytest.mark.django_db

CFG = Config(data_dir=Path("."), server_uid=uuid4(), timezone_name="UTC")


def test_the_check_is_registered() -> None:
    assert "il2ks.ops.login_checks" in CHECK_MODULES


def test_nothing_locked_is_ok() -> None:
    [finding] = list(login_lockout_check(CFG))

    assert finding.level is Level.OK
    assert "5 wrong passwords" in finding.detail
    assert "15 minutes" in finding.detail


def test_a_locked_account_at_an_address_is_listed_with_the_way_to_unlock(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    for _ in range(5):
        client.post(
            "/admin/login/", {"username": "boss", "password": "wrong"}, headers={"X-Forwarded-For": "203.0.113.5"}
        )

    [finding] = list(login_lockout_check(CFG))

    assert finding.level is Level.WARN
    assert "account boss from 203.0.113.5" in finding.detail
    assert "il2ks admin unlock" in finding.fix
    assert "Access attempts" not in finding.fix
    AccessAttempt.objects.all().delete()
    assert next(iter(login_lockout_check(CFG))).level is Level.OK


def test_an_ipv6_lock_is_listed_with_its_64_bucket(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    for _ in range(5):
        client.post(
            "/admin/login/", {"username": "boss", "password": "wrong"}, headers={"X-Forwarded-For": "2001:DB8:1:2::7"}
        )

    [finding] = list(login_lockout_check(CFG))

    assert "account boss from 2001:db8:1:2::/64" in finding.detail


def test_logins_without_a_visitor_address_are_a_warning_with_the_proxy_fix(client: Client) -> None:
    """Security audit 2026-11-14: when the HTTPS proxy sends no X-Forwarded-For (or is not listed), every visitor is
    one address and anybody can keep the admin locked out. Doctor says so as soon as such a login was seen."""
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    client.post("/admin/login/", {"username": "boss", "password": "wrong"})  # from 127.0.0.1, no X-Forwarded-For

    findings = list(login_lockout_check(CFG))

    warning = next(f for f in findings if "tell visitors apart" in f.title)
    assert warning.level is Level.WARN
    assert "X-Forwarded-For" in warning.fix
    assert "trusted_proxies" in warning.fix


def test_in_debug_mode_a_local_login_is_just_a_local_login(client: Client) -> None:
    import dataclasses

    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    client.post("/admin/login/", {"username": "boss", "password": "wrong"})

    findings = list(login_lockout_check(dataclasses.replace(CFG, debug=True)))

    assert not any("tell visitors apart" in f.title for f in findings)
