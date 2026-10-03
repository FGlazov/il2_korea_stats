"""The admin's password rules and login throttle (FR-ADM-1)."""

import pytest
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory

from il2ks.web import throttle

pytestmark = pytest.mark.django_db

LOGIN = "/admin/login/"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    cache.clear()


def attempt(client: Client, password: str, username: str = "boss", *, forwarded: str = "") -> HttpResponse:
    form = {"username": username, "password": password, "next": "/admin/"}
    return client.post(LOGIN, form, headers={"X-Forwarded-For": forwarded} if forwarded else None)


@pytest.mark.parametrize("weak", ["x", "12345678", "password", "qwerty123"])
def test_weak_passwords_are_refused_by_the_configured_validators(weak: str) -> None:
    """Admin forms and `createadmin` both run `AUTH_PASSWORD_VALIDATORS`; the setting must not be empty."""
    with pytest.raises(ValidationError):
        validate_password(weak, User(username="boss", email="boss@example.org"))


def test_a_good_password_passes_the_validators() -> None:
    validate_password("correct horse battery staple", User(username="boss"))


def test_ten_wrong_passwords_lock_the_address_out(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")

    for _ in range(throttle.MAX_FAILURES):
        assert attempt(client, "wrong").status_code == 200

    locked = attempt(client, "wrong")
    assert locked.status_code == 429
    assert locked["Retry-After"] == str(throttle.WINDOW_SECONDS)
    # even the right password is not checked while locked out
    assert attempt(client, "right-password-123").status_code == 429
    assert "_auth_user_id" not in client.session


def test_a_successful_login_resets_the_count(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    for _ in range(throttle.MAX_FAILURES - 1):
        attempt(client, "wrong")

    assert attempt(client, "right-password-123").status_code == 302
    client.logout()

    for _ in range(throttle.MAX_FAILURES - 1):
        assert attempt(client, "wrong").status_code == 200  # counting restarted from zero


def test_the_lockout_is_per_address(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    for _ in range(throttle.MAX_FAILURES):
        attempt(client, "wrong", forwarded="203.0.113.5")

    assert attempt(client, "wrong", forwarded="203.0.113.5").status_code == 429
    assert attempt(client, "right-password-123", forwarded="198.51.100.9").status_code == 302


def test_a_client_cannot_dodge_the_lockout_by_inventing_a_forwarded_for_header(client: Client) -> None:
    """Only the last entry (the one our proxy appended) counts."""
    for i in range(throttle.MAX_FAILURES):
        attempt(client, "wrong", forwarded=f"10.0.0.{i}, 203.0.113.5")

    assert attempt(client, "wrong", forwarded="10.9.9.9, 203.0.113.5").status_code == 429


def test_forwarded_for_is_ignored_unless_the_connection_is_from_loopback(client: Client) -> None:
    """A client that reaches the web server directly must not choose its bucket with its own X-Forwarded-For."""
    User.objects.create_superuser("boss", "boss@example.org", "right-password-123")
    direct = {"REMOTE_ADDR": "198.51.100.7"}

    for i in range(throttle.MAX_FAILURES):
        response = client.post(
            LOGIN, {"username": "boss", "password": "wrong"}, headers={"X-Forwarded-For": f"10.0.0.{i}"}, **direct
        )
        assert response.status_code == 200

    locked = client.post(
        LOGIN, {"username": "boss", "password": "wrong"}, headers={"X-Forwarded-For": "10.1.1.1"}, **direct
    )
    assert locked.status_code == 429


def test_client_address_uses_forwarded_for_only_from_loopback(rf: RequestFactory) -> None:
    assert throttle.client_address(rf.get("/", REMOTE_ADDR="::1", HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8")) == "5.6.7.8"
    assert throttle.client_address(rf.get("/", REMOTE_ADDR="127.0.0.1")) == "127.0.0.1"
    assert (
        throttle.client_address(rf.get("/", REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_FOR="1.2.3.4")) == "203.0.113.9"
    )


def test_reading_the_login_page_is_never_throttled(client: Client) -> None:
    for _ in range(throttle.MAX_FAILURES):
        attempt(client, "wrong")

    assert client.get(LOGIN).status_code == 200


def test_other_pages_are_not_counted(client: Client) -> None:
    for _ in range(throttle.MAX_FAILURES + 1):
        assert client.post("/admin/", {}).status_code in {200, 302}

    assert attempt(client, "wrong").status_code == 200
