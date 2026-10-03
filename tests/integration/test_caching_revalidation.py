"""Revalidation details of the data-version cache (TD-28): `If-None-Match: *`, always-revalidate, boot id, Vary."""

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory
from pytest_django.fixtures import Settings

from il2ks.web.caching import make_etag

pytestmark = pytest.mark.django_db


def vary_of(response: HttpResponse) -> set[str]:
    return {v.strip() for v in response["Vary"].split(",")}


def test_if_none_match_star_does_not_short_circuit_a_missing_page(client: Client) -> None:
    """`*` is for conditional writes: a page the view would 404 must not become a 304."""
    assert client.get("/no/such/page/", headers={"If-None-Match": "*"}).status_code == 404


def test_pages_are_revalidated_on_every_use(client: Client) -> None:
    """TD-28: hiding a cheater takes effect at once, so browsers may not reuse a page for even a minute unasked."""
    response = client.get("/")

    assert "max-age=0" in response["Cache-Control"]
    assert "must-revalidate" in response["Cache-Control"]
    assert "public" not in response["Cache-Control"]


def test_the_etag_is_the_same_in_every_worker_of_one_boot_and_new_after_a_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `[web] workers > 1` a conditional request may land on another worker (`serving.bootid`)."""
    request = RequestFactory().get("/players/")

    monkeypatch.setattr("il2ks.web.caching._BOOT_ID", "boot-1")
    worker_a = make_etag(request, 5)
    monkeypatch.setattr("il2ks.web.caching._BOOT_ID", "boot-1")
    worker_b = make_etag(request, 5)
    monkeypatch.setattr("il2ks.web.caching._BOOT_ID", "boot-2")

    assert worker_a == worker_b
    assert make_etag(request, 5) != worker_a


def test_vary_includes_cookie_only_when_a_language_cookie_is_sent(client: Client, settings: Settings) -> None:
    plain = client.get("/")
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    with_cookie = client.get("/")

    assert "Cookie" not in vary_of(plain)
    assert "Cookie" in vary_of(with_cookie)
    assert plain["ETag"] == with_cookie["ETag"]  # same language, same page
