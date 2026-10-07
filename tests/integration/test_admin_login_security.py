"""The admin's password rules and the login lockout by django-axes (FR-ADM-1, NFR-SEC-8)."""

import dataclasses
import logging
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from axes.models import AccessAttempt  # pyright: ignore[reportMissingTypeStubs]
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from il2ks.config import Config
from il2ks.serving.djsettings import trust_forwarded_for
from il2ks.web import login_protection
from il2ks.web.login_protection import client_address

pytestmark = pytest.mark.django_db

LOGIN = "/admin/login/"
LIMIT = 5  # [web] login_attempts default
ADDRESS_LIMIT = 3 * LIMIT  # one address, any accounts: looser, because many people can share an address
MINUTES = 15  # [web] login_lockout_minutes default
GOOD = "right-password-123"


def attempt(client: Client, password: str, username: str = "boss", *, forwarded: str = "") -> HttpResponse:
    form = {"username": username, "password": password, "next": "/admin/"}
    return client.post(LOGIN, form, headers={"X-Forwarded-For": forwarded} if forwarded else None)


@pytest.fixture
def boss() -> User:
    return User.objects.create_superuser("boss", "boss@example.org", GOOD)


@pytest.mark.parametrize("weak", ["x", "12345678", "password", "qwerty123"])
def test_weak_passwords_are_refused_by_the_configured_validators(weak: str) -> None:
    """Admin forms and `createadmin` both run `AUTH_PASSWORD_VALIDATORS`; the setting must not be empty."""
    with pytest.raises(ValidationError):
        validate_password(weak, User(username="boss", email="boss@example.org"))


def test_a_good_password_passes_the_validators() -> None:
    validate_password("correct horse battery staple", User(username="boss"))


def test_five_wrong_passwords_lock_the_account_at_that_address(client: Client, boss: User) -> None:
    for _ in range(LIMIT - 1):
        assert attempt(client, "wrong").status_code == 200

    locked = attempt(client, "wrong")  # the fifth wrong password is already answered with the lockout page
    assert locked.status_code == 429
    assert locked["Retry-After"] == str(MINUTES * 60)
    assert f"Try again in {MINUTES} minutes" in locked.content.decode()
    # even the right password is not checked while locked
    assert attempt(client, GOOD).status_code == 429
    assert "_auth_user_id" not in client.session


def test_a_stranger_cannot_lock_the_admin_out_from_every_address(client: Client, boss: User) -> None:
    """H3: the lock is the pair (account, address); wrong passwords from one address never lock the admin elsewhere."""
    for _ in range(LIMIT):
        attempt(client, "wrong", forwarded="203.0.113.5")
    assert attempt(client, GOOD, forwarded="203.0.113.5").status_code == 429  # the pair is locked

    assert attempt(client, GOOD, forwarded="198.51.100.9").status_code == 302


def test_a_stranger_rotating_addresses_still_cannot_lock_the_account(client: Client, boss: User) -> None:
    for i in range(3 * LIMIT):
        attempt(client, "wrong", forwarded=f"203.0.113.{i + 1}")

    assert attempt(client, GOOD, forwarded="198.51.100.9").status_code == 302


def test_one_address_is_locked_after_the_looser_address_limit_for_any_accounts(client: Client, boss: User) -> None:
    User.objects.create_superuser("deputy", "deputy@example.org", GOOD)
    for i in range(ADDRESS_LIMIT - 1):
        assert attempt(client, "wrong", username=f"guess{i}", forwarded="203.0.113.5").status_code == 200
    assert attempt(client, GOOD, username="deputy", forwarded="203.0.113.5").status_code == 302
    client.logout()

    assert attempt(client, "wrong", username="guess-last", forwarded="203.0.113.5").status_code == 429
    assert attempt(client, GOOD, username="deputy", forwarded="203.0.113.5").status_code == 429
    assert attempt(client, GOOD, username="deputy", forwarded="198.51.100.9").status_code == 302


def test_a_few_failures_for_several_accounts_do_not_lock_the_address(client: Client, boss: User) -> None:
    """Five wrong passwords for several accounts from one address stay below the address limit: nobody is locked."""
    for i in range(LIMIT):
        assert attempt(client, "wrong", username=f"guess{i}", forwarded="203.0.113.5").status_code == 200

    assert attempt(client, GOOD, forwarded="203.0.113.5").status_code == 302


def test_a_successful_login_resets_the_count(client: Client, boss: User) -> None:
    for _ in range(LIMIT - 1):
        attempt(client, "wrong")

    assert attempt(client, GOOD).status_code == 302
    client.logout()

    for _ in range(LIMIT - 1):
        assert attempt(client, "wrong").status_code == 200  # counting restarted from zero


def test_the_lock_ends_after_the_lockout_time(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong")
    assert attempt(client, GOOD).status_code == 429

    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=MINUTES, seconds=5))

    assert attempt(client, GOOD).status_code == 302


def test_the_page_counts_down_the_minutes(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong")
    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=10))

    assert "Try again in 5 minutes" in attempt(client, GOOD).content.decode()


def test_the_limit_comes_from_the_settings(client: Client, boss: User, settings: SettingsWrapper) -> None:
    settings.AXES_FAILURE_LIMIT = 2
    assert attempt(client, "wrong").status_code == 200

    assert attempt(client, "wrong").status_code == 429


def test_a_client_cannot_dodge_the_lockout_by_inventing_a_forwarded_for_header(client: Client, boss: User) -> None:
    """Only the last entry (the one our proxy appended) counts."""
    for i in range(ADDRESS_LIMIT):
        attempt(client, "wrong", username=f"guess{i}", forwarded=f"10.0.0.{i}, 203.0.113.5")

    assert attempt(client, "wrong", username="other", forwarded="10.9.9.9, 203.0.113.5").status_code == 429


def test_forwarded_for_is_ignored_unless_the_connection_is_from_loopback(client: Client, boss: User) -> None:
    """A client that reaches the web server directly must not choose its address with its own X-Forwarded-For."""
    client.defaults["REMOTE_ADDR"] = "198.51.100.7"  # a direct connection, not via the proxy

    for i in range(ADDRESS_LIMIT):
        client.post(LOGIN, {"username": f"guess{i}", "password": "wrong"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})

    locked = client.post(LOGIN, {"username": "another", "password": "x"}, headers={"X-Forwarded-For": "10.1.1.1"})
    assert locked.status_code == 429


def test_forwarded_for_is_ignored_when_the_site_is_not_behind_a_proxy(
    client: Client, boss: User, settings: SettingsWrapper
) -> None:
    """Development (`debug`): there is no proxy, so even a loopback connection's header is the visitor's own."""
    settings.IL2KS_TRUST_FORWARDED_FOR = False
    for i in range(ADDRESS_LIMIT):
        attempt(client, "wrong", username=f"guess{i}", forwarded=f"10.0.0.{i}")

    assert attempt(client, "wrong", username="another", forwarded="10.9.9.9").status_code == 429


def test_client_address_rules(rf: RequestFactory, settings: SettingsWrapper) -> None:
    settings.IL2KS_TRUST_FORWARDED_FOR = True
    assert client_address(rf.get("/", REMOTE_ADDR="::1", HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8")) == "5.6.7.8"
    assert client_address(rf.get("/", REMOTE_ADDR="127.0.0.1")) == "127.0.0.1"
    assert client_address(rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="not an address")) == "127.0.0.1"
    assert client_address(rf.get("/", REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_FOR="1.2.3.4")) == "203.0.113.9"
    settings.IL2KS_TRUST_FORWARDED_FOR = False
    assert client_address(rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4")) == "127.0.0.1"


def test_the_trust_rule_is_the_one_of_the_proxy_ssl_header() -> None:
    production = Config(data_dir=Path("."), server_uid=uuid4(), timezone_name="UTC")

    assert trust_forwarded_for(production) is True
    assert trust_forwarded_for(dataclasses.replace(production, debug=True)) is False


def test_the_lockout_page_is_translated(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong")

    response = client.post(LOGIN, {"username": "boss", "password": GOOD}, headers={"Accept-Language": "de"})

    assert response.status_code == 429
    assert "Try again" not in response.content.decode()


def test_failures_and_lockouts_are_logged_with_fields(
    client: Client, boss: User, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=login_protection.__name__):
        for _ in range(LIMIT):
            attempt(client, "wrong", forwarded="203.0.113.5")

    events = [
        (r.__dict__["event"], r.__dict__["username"], r.__dict__["ip_address"])
        for r in caplog.records
        if "event" in r.__dict__
    ]
    assert events.count(("admin_login_failed", "boss", "203.0.113.5")) == LIMIT
    assert ("admin_login_locked", "boss", "203.0.113.5") in events


def test_current_lockouts_lists_locked_pairs_and_addresses(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong", forwarded="203.0.113.5")
    attempt(client, "wrong", username="other", forwarded="198.51.100.9")  # one failure: not locked
    for i in range(ADDRESS_LIMIT):
        attempt(client, "wrong", username=f"guess{i}", forwarded="192.0.2.77")

    locks = login_protection.current_lockouts()

    assert [(lock.kind, lock.name) for lock in locks] == [
        ("account", "boss from 203.0.113.5"),
        ("address", "192.0.2.77"),
    ]
    assert all(1 <= lock.minutes_left <= MINUTES for lock in locks)


def test_unlock_by_account_address_or_everything(client: Client, boss: User) -> None:
    for address in ("203.0.113.5", "198.51.100.9"):
        for _ in range(LIMIT):
            attempt(client, "wrong", forwarded=address)

    assert login_protection.unlock(ip="203.0.113.5") == 1
    assert [lock.name for lock in login_protection.current_lockouts()] == ["boss from 198.51.100.9"]
    assert login_protection.unlock(username="boss", ip="198.51.100.9") == 1
    assert login_protection.current_lockouts() == []
    attempt(client, "wrong", forwarded="203.0.113.5")
    assert login_protection.unlock(username="boss") == 1
    attempt(client, "wrong", forwarded="203.0.113.5")
    assert login_protection.unlock() == 1  # nothing given: everything
    assert AccessAttempt.objects.count() == 0


def test_the_lockout_page_counts_the_pair_not_unrelated_attempts(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong", forwarded="203.0.113.5")
    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=10))
    attempt(client, "wrong", username="nobody", forwarded="198.51.100.9")  # a fresh attempt of someone else

    assert "Try again in 5 minutes" in attempt(client, GOOD, forwarded="203.0.113.5").content.decode()


def test_an_admin_unlocks_by_deleting_the_attempt_rows(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong")
    assert attempt(client, GOOD).status_code == 429

    AccessAttempt.objects.all().delete()

    assert attempt(client, GOOD).status_code == 302
    assert login_protection.current_lockouts() == []


def test_the_attempts_are_listed_in_the_admin(client: Client, boss: User) -> None:
    for _ in range(LIMIT):
        attempt(client, "wrong")
    other = Client(REMOTE_ADDR="198.51.100.1")
    User.objects.create_superuser("root", "root@example.org", GOOD)
    assert other.post(LOGIN, {"username": "root", "password": GOOD, "next": "/admin/"}).status_code == 302

    page = other.get("/admin/axes/accessattempt/")

    assert page.status_code == 200
    assert "boss" in page.content.decode()


def test_reading_the_login_page_is_never_locked(client: Client, boss: User) -> None:
    for _ in range(LIMIT + 1):
        attempt(client, "wrong")

    assert client.get(LOGIN).status_code == 200


def test_axes_tables_come_with_the_migrations_that_setup_and_upgrades_run() -> None:
    """`il2ks setup` and every upgrade call `migrate` for all apps (`ops.migrate`): axes's migrations are in the plan
    the executor builds from the leaf nodes, so a database made by 0.1.0 gets the lockout tables on upgrade."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    assert any(app == "axes" for app, _name in executor.loader.graph.leaf_nodes())
    assert "axes_accessattempt" in connection.introspection.table_names()
