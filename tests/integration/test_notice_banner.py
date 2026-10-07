"""The site-wide notice banner (roadmap 0.2.0): text, level, optional expiry, under the header of every public page.

Synthetic data only."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from il2ks.db.models import DataVersion, SiteSettings
from il2ks.db.site import current_data_version, get_site_settings
from tests.integration.test_admin import settings_form, settings_url

pytestmark = pytest.mark.django_db

PAGES = ["/", "/players/", "/aircraft/", "/missions/", "/sorties/", "/leaderboards/"]


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def set_notice(text: str, level: str = "info", until: object = None) -> None:
    row = get_site_settings()
    row.notice_text = text
    row.notice_level = level
    row.notice_until = until  # pyright: ignore[reportAttributeAccessIssue]
    row.save()


def banner(html: str) -> str:
    start = html.find('class="page site-notice"')
    return "" if start < 0 else html[start : html.index("</div>", start)]


def test_no_text_shows_nothing(client: Client) -> None:
    assert "site-notice" not in client.get("/").content.decode()


@pytest.mark.parametrize("url", PAGES)
def test_the_banner_is_under_the_header_on_every_public_page(client: Client, url: str) -> None:
    set_notice("Event tonight at 20:00")

    html = client.get(url).content.decode()

    assert "Event tonight at 20:00" in banner(html)
    assert html.index("</header>") < html.index("site-notice") < html.index('<main id="main"')


def test_levels_use_the_notice_component_styles(client: Client) -> None:
    set_notice("Rules changed", "warning")
    assert "notice--warning" in banner(client.get("/").content.decode())
    set_notice("Rules changed", "info")
    assert "notice--info" in banner(client.get("/").content.decode())


def test_the_text_is_escaped(client: Client) -> None:
    set_notice("<script>alert(1)</script> & <b>bold</b>")

    html = client.get("/").content.decode()

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in banner(html)


def test_whitespace_only_counts_as_empty(client: Client) -> None:
    set_notice("   ")
    assert "site-notice" not in client.get("/").content.decode()


def test_an_expiry_in_the_future_shows_the_banner_and_marks_it_for_the_client_script(client: Client) -> None:
    until = timezone.now() + timedelta(hours=2)
    set_notice("Maintenance", until=until)

    shown = banner(client.get("/").content.decode())

    assert "Maintenance" in shown
    assert f'data-il2-until="{until.isoformat()}"' in shown  # localtime.js hides a cached copy once it has passed


def test_a_banner_without_expiry_has_no_hide_marker(client: Client) -> None:
    set_notice("Maintenance")
    assert "data-il2-until" not in banner(client.get("/").content.decode())


def test_a_past_expiry_hides_the_banner(client: Client) -> None:
    set_notice("Old news", until=timezone.now() - timedelta(minutes=1))
    assert "site-notice" not in client.get("/").content.decode()


def test_the_expiry_passing_hides_it_even_when_nothing_else_changed(client: Client) -> None:
    """A cached page (ETag from the data version) stays the same when the moment passes: it carries the expiry for
    localtime.js, and a fresh render after the moment shows nothing, without any save in between."""
    set_notice("Soon over", until=timezone.now() + timedelta(seconds=30))
    version = current_data_version()

    first = client.get("/").content.decode()
    SiteSettings.objects.filter(pk=1).update(notice_until=timezone.now() - timedelta(seconds=1))  # time passes
    second = client.get("/").content.decode()

    assert "Soon over" in first
    assert "site-notice" not in second
    assert current_data_version() == version


def test_saving_in_the_admin_stores_the_notice_and_bumps_the_data_version(admin: Client) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(
        settings_url(),
        settings_form(
            notice_text="Event tonight", notice_level="warning", notice_until_0="2099-01-01", notice_until_1="20:00:00"
        ),
    )

    assert response.status_code == 302
    row = get_site_settings()
    assert (row.notice_text, row.notice_level) == ("Event tonight", "warning")
    assert row.notice_until is not None
    assert row.notice_until.isoformat().startswith("2099-01-01T20:00:00")
    assert current_data_version() > before


def test_a_post_without_the_notice_fields_keeps_the_stored_level(admin: Client) -> None:
    set_notice("Keep", "warning")

    assert admin.post(settings_url(), settings_form(notice_text="Keep")).status_code == 302

    assert get_site_settings().notice_level == "warning"


def test_the_text_is_limited_to_300_characters(admin: Client) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(notice_text="x" * 301, notice_level="info"))

    assert response.status_code == 200  # the form comes back with the error
    assert get_site_settings().notice_text == ""
    assert DataVersion.objects.count() <= 1


def test_the_admin_fields_explain_themselves(admin: Client) -> None:
    get_site_settings()
    html = admin.get(settings_url()).content.decode()

    assert "same in every language" in html
    assert "disappears by itself" in html
