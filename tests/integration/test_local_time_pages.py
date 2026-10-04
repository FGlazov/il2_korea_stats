"""The layout loads the local-time script and says which zone times are in (FR-WEB-17, TD-15)."""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_base_loads_the_local_time_script_deferred_and_has_the_zone_note(client: Client) -> None:
    html = client.get("/").content.decode()
    assert 'localtime.js" defer></script>' in html
    assert "data-il2-tz-note" in html
    assert "Times are shown in UTC." in html  # what a visitor without JavaScript reads
    assert "__TZ__" in html  # the script's template for the converted note


@pytest.mark.django_db
def test_markup_is_the_same_whoever_asks(client: Client) -> None:
    """The server never looks at a time zone, so a cached page is right for everybody."""
    first = client.get("/missions/", headers={"Accept-Language": "en"}).content
    second = client.get("/missions/", headers={"Accept-Language": "en", "Time-Zone": "Asia/Tokyo"}).content
    assert first == second
