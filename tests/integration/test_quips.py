"""Admin-configurable quips on the pages and in the admin (FR-WEB-23): defaults unchanged, switches, escaping, cache."""

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape

from il2ks.db.models import SiteSettings
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.web import flavor, quips
from il2ks.web.quips import CustomQuip, QuipConfig
from tests.integration.test_shame_quip import page, pk, seed

pytestmark = pytest.mark.django_db

CLEAN = 9  # a pilot with nothing on record: the "shame_clean" spot (see test_shame_quip)
QUIPS_URL = "/admin/quips/"


def configure(config: QuipConfig) -> None:
    row = get_site_settings()
    row.quips_enabled = config.enabled
    row.quips = config.to_json()
    row.save()


def defaults(spot: str) -> list[str]:
    return [escape(str(v)) for v in flavor.SPOTS[spot]]


def shows_default(body: str, spot: str) -> bool:
    return any(line in body for line in defaults(spot))


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_without_admin_choices_the_page_shows_the_built_in_quip_as_before(client: Client) -> None:
    seed()
    assert escape(str(flavor.pick("shame_clean", pk(CLEAN)))) in page(client, CLEAN)


def test_the_global_switch_turns_every_quip_off(client: Client) -> None:
    seed()
    configure(QuipConfig(enabled=False))
    body = page(client, CLEAN)
    assert not shows_default(body, "shame_clean")
    assert "shame__quip" not in body  # no empty paragraph either


def test_a_spot_can_be_turned_off_alone(client: Client) -> None:
    seed()
    configure(QuipConfig(modes={"shame_clean": "off"}))
    assert "shame__quip" not in page(client, CLEAN)
    assert shows_default(page(client, 1), "shame_taxi_p90")  # another spot is untouched


def test_hiding_a_default_removes_only_that_line(client: Client) -> None:
    seed()
    shown = str(flavor.pick("shame_clean", pk(CLEAN)))
    key = quips.default_keys("shame_clean")[[str(v) for v in flavor.SPOTS["shame_clean"]].index(shown)]
    configure(QuipConfig(hidden={"shame_clean": frozenset({key})}))
    body = page(client, CLEAN)
    assert escape(shown) not in body
    assert shows_default(body, "shame_clean")  # one of the others took over


def test_custom_only_shows_the_own_line_escaped(client: Client) -> None:
    seed()
    configure(
        QuipConfig(
            modes={"shame_clean": "custom_only"}, custom=(CustomQuip("shame_clean", "<script>alert(1)</script>"),)
        )
    )
    body = page(client, CLEAN)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert not shows_default(body, "shame_clean")


def test_a_custom_quip_for_one_language_shows_only_there(client: Client) -> None:
    seed()
    configure(
        QuipConfig(
            modes={"shame_clean": "custom_only"},
            custom=(CustomQuip("shame_clean", "Nur auf Deutsch", "de"), CustomQuip("shame_clean", "Everywhere")),
        )
    )
    english = page(client, CLEAN)
    assert "Everywhere" in english
    assert "Nur auf Deutsch" not in english
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
    german = page(client, CLEAN)
    assert "Nur auf Deutsch" in german or "Everywhere" in german
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "fr"
    assert "Nur auf Deutsch" not in page(client, CLEAN)


def test_quips_cost_no_extra_query(client: Client) -> None:
    seed()
    url = f"/players/{pk(CLEAN)}/?tour=all"

    def count() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(url).status_code == 200
        return len(ctx.captured_queries)

    plain = count()
    configure(
        QuipConfig(
            modes={"shame_clean": "defaults_and_custom"},
            hidden={"shame_clean": frozenset({"x"})},
            custom=(CustomQuip("shame_clean", "Mine"),),
        )
    )
    assert count() == plain


def test_a_fresh_database_still_shows_the_defaults(client: Client) -> None:
    assert SiteSettings.objects.count() == 0
    assert client.get("/").status_code == 200


def test_the_admin_page_lists_every_spot_with_its_defaults(admin: Client) -> None:
    body = admin.get(QUIPS_URL).content.decode()
    for spot in flavor.SPOTS:
        assert f'id="spot-{spot}"' in body
        assert escape(str(flavor.SPOT_DESCRIPTIONS[spot])) in body
    assert escape("A clean sheet so far. The runway thanks you.") in body


def test_the_quips_page_needs_the_site_settings_permission(client: Client) -> None:
    assert client.get(QUIPS_URL).status_code == 302
    client.force_login(User.objects.create_user("staff", "s@example.org", "x", is_staff=True))
    assert client.get(QUIPS_URL).status_code == 403


def post(admin: Client, **fields: str | list[str]) -> str:
    data: dict[str, str | list[str]] = {"enabled": "on", **fields}
    return admin.post(QUIPS_URL, data, follow=True).content.decode()


def test_saving_modes_hides_and_custom_lines_from_the_admin_page(admin: Client, client: Client) -> None:
    key = quips.default_keys("shame_clean")[0]
    post(
        admin,
        **{
            "mode-shame_clean": "defaults_and_custom",
            "hide-shame_clean": [key],
            "new-shame_clean-text": "  Our   own\nline ",
            "new-shame_clean-lang": "de",
            "mode-top_pilot": "off",
        },
    )
    config = QuipConfig.from_row(True, get_site_settings().quips)
    assert config.mode("shame_clean") == "defaults_and_custom"
    assert config.mode("top_pilot") == "off"
    assert config.hidden["shame_clean"] == {key}
    assert config.custom == (CustomQuip("shame_clean", "Our own line", "de"),)
    shown = admin.get(QUIPS_URL).content.decode()
    assert 'value="Our own line"' in shown


def test_saving_without_the_switch_turns_quips_off(admin: Client) -> None:
    admin.post(QUIPS_URL, {})
    assert get_site_settings().quips_enabled is False
    post(admin)
    assert get_site_settings().quips_enabled is True


def test_a_custom_line_can_be_edited_disabled_and_deleted(admin: Client) -> None:
    configure(
        QuipConfig(
            custom=(CustomQuip("top_pilot", "One"), CustomQuip("top_pilot", "Two"), CustomQuip("tour_empty", "Three"))
        )
    )
    post(
        admin,
        **{
            "c0-spot": "top_pilot",
            "c0-text": "One edited",
            "c0-lang": "",
            "c1-spot": "top_pilot",
            "c1-text": "Two",
            "c1-lang": "",
            "c1-del": "on",
            "c2-spot": "tour_empty",
            "c2-text": "Three",
            "c2-lang": "fr",
        },
    )
    custom = QuipConfig.from_row(True, get_site_settings().quips).custom
    assert custom == (CustomQuip("top_pilot", "One edited", "", False), CustomQuip("tour_empty", "Three", "fr", False))


def test_a_too_long_line_or_unknown_language_saves_nothing(admin: Client) -> None:
    body = post(admin, **{"new-top_pilot-text": "x" * (quips.MAX_LEN + 1)})
    assert str(quips.MAX_LEN) in body
    assert get_site_settings().quips == {}
    post(admin, **{"new-top_pilot-text": "ok", "new-top_pilot-lang": "xx"})
    assert get_site_settings().quips == {}


def test_a_rejected_new_line_comes_back_in_its_field_with_the_error(admin: Client) -> None:
    """The page re-renders the posted text, not the stored configuration, so the admin can fix it instead of retyping."""
    too_long = "Typed with care " + "x" * quips.MAX_LEN
    body = post(admin, **{"new-top_pilot-text": too_long})
    assert f'name="new-top_pilot-text" value="{too_long}"' in body
    assert str(quips.MAX_LEN) in body

    body = post(admin, **{"new-top_pilot-text": "Wrong language", "new-top_pilot-lang": "xx"})
    assert 'name="new-top_pilot-text" value="Wrong language"' in body
    assert get_site_settings().quips == {}


def test_a_stale_hide_is_shown_kept_and_can_be_forgotten(admin: Client) -> None:
    configure(QuipConfig(hidden={"top_pilot": frozenset({"Reworded line"})}))
    assert "Reworded line" in admin.get(QUIPS_URL).content.decode()
    post(admin)  # saving without touching it keeps it
    assert QuipConfig.from_row(True, get_site_settings().quips).hidden == {"top_pilot": frozenset({"Reworded line"})}
    post(admin, **{"forget-top_pilot": ["Reworded line"]})
    assert QuipConfig.from_row(True, get_site_settings().quips).hidden == {}


def test_a_hide_of_a_key_that_is_not_a_default_is_not_stored(admin: Client) -> None:
    post(admin, **{"hide-top_pilot": ["made up"]})
    assert get_site_settings().quips == {"modes": {}, "hidden": {}, "custom": []}


def test_saving_bumps_the_data_version_and_changes_the_cached_page(admin: Client, client: Client) -> None:
    seed()
    url = f"/players/{pk(CLEAN)}/?tour=all"
    before = client.get(url)
    version = current_data_version()
    post(admin, **{"mode-shame_clean": "off"})
    assert current_data_version() == version + 1
    after = client.get(url)
    assert after.headers["ETag"] != before.headers["ETag"]
    assert "shame__quip" not in after.content.decode()


def test_the_admin_index_links_to_the_page(admin: Client) -> None:
    assert QUIPS_URL in admin.get("/admin/").content.decode()
