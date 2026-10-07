"""`il2ks doctor` lists the locked admin accounts and addresses (NFR-SEC-8)."""

from pathlib import Path
from uuid import uuid4

import pytest
from axes.models import AccessAttempt  # pyright: ignore[reportMissingTypeStubs]
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
