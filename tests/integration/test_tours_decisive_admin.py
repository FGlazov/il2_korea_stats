"""The admin's Tours page: the option "Start a new tour when a mission is won by one side" (default off), saved as the
admin's choice and applied by `watch` / `rebuild-aggregates --retour` (maintainer request 2026-10-05, doc 16)."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from il2ks.db.models import Mission
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.ingest.tours import applied_on_win, on_win_pending, wanted_on_win
from tests.integration.test_tours_decisive import at, put

pytestmark = pytest.mark.django_db

URL = "/admin/tours/"


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_the_option_is_off_by_default_and_the_page_needs_the_permission(client: Client) -> None:
    assert not wanted_on_win()
    assert not applied_on_win()
    assert Client().get(URL).status_code == 302  # to the login
    clerk = client
    clerk.force_login(User.objects.create_user("clerk", is_staff=True, password="x"))
    assert clerk.get(URL).status_code == 403


def test_the_page_shows_the_option_and_how_many_missions_have_a_result(admin: Client) -> None:
    put(at(2026, 10, 1, 8), winner=1)
    put(at(2026, 10, 1, 12), draw=True)
    put(at(2026, 10, 1, 16))
    page = admin.get(URL).content.decode()

    assert 'name="tour_on_win"' in page
    assert " checked" not in page.split('name="tour_on_win"', 1)[1].split(">", 1)[0]
    assert "No tour reassignment is pending" in page
    assert "Won by one side: 1" in page
    assert "Draws: 1" in page
    assert "No result: 1" in page


def test_saving_the_choice_is_pending_until_the_tours_are_reassigned(admin: Client) -> None:
    version = current_data_version()

    response = admin.post(URL, {"tour_on_win": "on"})

    assert response.status_code == 302
    assert wanted_on_win()
    assert not applied_on_win()  # only a retour (watch, rebuild-aggregates --retour) applies it
    assert on_win_pending()
    assert current_data_version() > version
    assert "A reassignment of the tours is pending" in admin.get(URL).content.decode()

    admin.post(URL, {})  # the box unticked again
    assert not wanted_on_win()
    assert not on_win_pending()


def test_an_untouched_page_post_changes_nothing_else(admin: Client) -> None:
    get_site_settings()
    put(at(2026, 10, 1, 8), winner=1)
    before = Mission.objects.get().tour_id

    admin.post(URL, {"tour_on_win": "on"})

    assert Mission.objects.get().tour_id == before  # tours move only when the retour runs
