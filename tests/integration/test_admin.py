"""The admin (FR-ADM-1..5): site settings, logo upload, hiding, read-only ingested rows, ingestion status."""

import io
import re
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks, Settings

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.db.models import (
    CompletionReason,
    Country,
    GameObject,
    IngestRun,
    IngestStatus,
    Mission,
    NavLink,
    ObjectClass,
    Player,
    PlayerName,
    SiteSettings,
)
from il2ks.db.site import current_data_version, get_site_settings
from tests.factories import mission, save, sortie

pytestmark = pytest.mark.django_db

NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(
    hours=1
)  # recent, so the status page's 30-day window has them


@pytest.fixture
def media_root(tmp_path: Path, settings: Settings) -> Path:
    settings.MEDIA_ROOT = tmp_path / "media"
    return tmp_path / "media"


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def png(width: int = 120, height: int = 40, colour: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def settings_url() -> str:
    return "/admin/il2ks_db/sitesettings/1/change/"


def settings_form(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "site_title": "Korea Fighters",
        "server_name": "Fighter Server",
        "description": "Welcome",
        "redfor_name": "Red",
        "blufor_name": "Blue",
        "redfor_emblem": "plaaf",
        "blufor_emblem": "rokaf",
    }
    data.update(
        nav_formset([("Discord", "https://discord.gg/example", "discord"), ("Homepage", "http://example.org/", "")])
    )
    data.update(overrides)
    return data


def nav_formset(links: list[tuple[str, str, str]], *, initial: int = 0) -> dict[str, object]:
    """POST data for the navigation-link inline: `links` are (label, url, icon) in row order; the first `initial` rows
    are existing ones (read back from the database: Postgres does not restart its id sequences between tests)."""
    ids = list(NavLink.objects.order_by("position", "id").values_list("pk", flat=True))[:initial]
    data: dict[str, object] = {
        "nav_links-TOTAL_FORMS": len(links),
        "nav_links-INITIAL_FORMS": initial,
        "nav_links-MIN_NUM_FORMS": 0,
        "nav_links-MAX_NUM_FORMS": 1000,
    }
    for number, (label, url, icon) in enumerate(links):
        data[f"nav_links-{number}-label"] = label
        data[f"nav_links-{number}-url"] = url
        data[f"nav_links-{number}-icon"] = icon
        data[f"nav_links-{number}-position"] = number + 1
        if number < initial:
            data[f"nav_links-{number}-id"] = ids[number]
            data[f"nav_links-{number}-site"] = 1
    return data


# --- access ---


def test_admin_needs_a_staff_login(client: Client) -> None:
    for url in ("/admin/", "/admin/ingestion/", "/admin/il2ks_db/player/"):
        response = client.get(url)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]


def test_staff_without_permissions_cannot_see_the_status_page(client: Client) -> None:
    client.force_login(User.objects.create_user("clerk", password="x", is_staff=True))

    assert client.get("/admin/ingestion/").status_code == 403


def test_admin_titles_follow_site_settings(admin: Client) -> None:
    SiteSettings.objects.update_or_create(pk=1, defaults={"site_title": "Fighter Wing Stats"})

    body = admin.get("/admin/").content.decode()

    assert "Fighter Wing Stats" in body
    assert "Django administration" not in body
    assert "/admin/ingestion/" in body  # linked from the index


def test_login_page_works_before_any_site_settings_exist(client: Client) -> None:
    assert not SiteSettings.objects.exists()

    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert not SiteSettings.objects.exists(), "an anonymous request must not write"


# --- site settings ---


def test_site_settings_changelist_goes_to_the_single_form(admin: Client) -> None:
    response = admin.get("/admin/il2ks_db/sitesettings/")

    assert response.status_code == 302
    assert response["Location"] == settings_url()
    assert admin.get(settings_url()).status_code == 200


def test_site_settings_cannot_be_added_or_deleted(admin: Client) -> None:
    get_site_settings()

    assert admin.get("/admin/il2ks_db/sitesettings/add/").status_code == 403
    assert admin.get("/admin/il2ks_db/sitesettings/1/delete/").status_code == 403
    assert (
        admin.post("/admin/il2ks_db/sitesettings/", {"action": "delete_selected", "_selected_action": 1}).status_code
        == 302
    )
    assert SiteSettings.objects.filter(pk=1).exists()


def test_saving_site_settings(admin: Client, media_root: Path) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(heading_font="serif", body_font="humanist"))

    assert response.status_code == 302, getattr(response, "context", None) and response.context["adminform"].form.errors
    row = SiteSettings.objects.get(pk=1)
    assert row.site_title == "Korea Fighters"
    assert row.server_name == "Fighter Server"
    assert (row.heading_font, row.body_font) == ("serif", "humanist")
    assert row.links == [
        {"label": "Discord", "url": "https://discord.gg/example", "icon": "discord"},
        {"label": "Homepage", "url": "http://example.org/", "icon": ""},
    ]
    assert [(link.label, link.position) for link in NavLink.objects.order_by("position")] == [
        ("Discord", 1),
        ("Homepage", 2),
    ]
    assert (row.redfor_name, row.blufor_name) == ("Red", "Blue")
    assert (row.redfor_emblem, row.blufor_emblem) == ("plaaf", "rokaf")  # FR-ADM-2, doc 15
    assert current_data_version() > before


def test_the_form_lists_the_links_and_recommends_a_number(admin: Client) -> None:
    site = get_site_settings()
    NavLink.objects.create(site=site, label="Our Discord", url="https://discord.gg/example", icon="discord", position=1)

    body = admin.get(settings_url()).content.decode()

    assert 'value="Our Discord"' in body
    assert "We recommend at most 3 extra links" in body


def test_links_are_reordered_and_renumbered(admin: Client) -> None:
    site = get_site_settings()
    NavLink.objects.create(site=site, label="A", url="https://a.example/", position=1)
    NavLink.objects.create(site=site, label="B", url="https://b.example/", position=2)
    data = settings_form(
        **nav_formset(
            [("A", "https://a.example/", ""), ("B", "https://b.example/", ""), ("C", "https://c.example/", "forum")],
            initial=2,
        )
    )
    data["nav_links-0-position"] = 30  # move A behind B; the new C stays last
    data["nav_links-1-position"] = 10
    data["nav_links-2-position"] = 40

    assert admin.post(settings_url(), data).status_code == 302

    assert [link["label"] for link in SiteSettings.objects.get(pk=1).links] == ["B", "A", "C"]
    assert list(NavLink.objects.order_by("position").values_list("label", "position")) == [("B", 1), ("A", 2), ("C", 3)]


def test_a_link_can_be_deleted(admin: Client) -> None:
    site = get_site_settings()
    NavLink.objects.create(site=site, label="A", url="https://a.example/", position=1)
    data = settings_form(**nav_formset([("A", "https://a.example/", "")], initial=1))
    data["nav_links-0-DELETE"] = "on"

    assert admin.post(settings_url(), data).status_code == 302
    assert SiteSettings.objects.get(pk=1).links == []
    assert not NavLink.objects.exists()


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.org/",
        "mailto:someone@example.org",
        "/admin/",
        "//example.org/",
        "https://",
        "https://example.org/a b",
        "https://example.org/\nx",
        "https://[bad",
        "https://example.org/" + "x" * 1981,  # 2001 characters
    ],
)
def test_bad_link_addresses_are_refused(admin: Client, url: str) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(**nav_formset([("Click", url, "")])))

    assert response.status_code == 200
    assert response.context["inline_admin_formsets"][0].formset.errors[0]
    assert SiteSettings.objects.get(pk=1).links == []
    assert not NavLink.objects.exists()
    assert current_data_version() == before


def test_a_link_needs_a_label_and_a_known_icon(admin: Client) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(**nav_formset([("", "https://a.example/", "")])))
    assert response.status_code == 200
    response = admin.post(settings_url(), settings_form(**nav_formset([("A", "https://a.example/", "../x")])))
    assert response.status_code == 200
    assert not NavLink.objects.exists()


def test_too_many_links_are_refused(admin: Client) -> None:
    get_site_settings()
    links = [(f"L{i}", f"https://example.org/{i}", "") for i in range(31)]

    response = admin.post(settings_url(), settings_form(**nav_formset(links)))

    assert response.status_code == 200
    assert not NavLink.objects.exists()


# --- colours and fonts ---


def test_saving_colours_stores_only_the_boxes_that_are_filled(admin: Client) -> None:
    get_site_settings()

    response = admin.post(
        settings_url(),
        settings_form(
            **{"theme__accent__light": "#1a73e8", "theme__accent__dark": "#8ab4f8", "theme__surface-2__dark": "#202020"}
        ),
    )

    assert response.status_code == 302
    assert SiteSettings.objects.get(pk=1).theme == {
        "light": {"accent": "#1A73E8"},
        "dark": {"accent": "#8AB4F8", "surface-2": "#202020"},
    }


@pytest.mark.parametrize(
    "colour", ["red", "#12345", "#1234567", "#GGGGGG", "123456", "#12 456", "#fff", "#abc}body{x:y"]
)
def test_bad_colours_are_refused(admin: Client, colour: str) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(**{"theme__bg__dark": colour}))

    assert response.status_code == 200
    assert "theme" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).theme == {}
    assert current_data_version() == before


def test_emptying_the_boxes_resets_to_the_default(admin: Client) -> None:
    row = get_site_settings()
    row.theme = {"light": {"accent": "#112233"}, "dark": {}}
    row.save()

    assert admin.post(settings_url(), settings_form()).status_code == 302
    assert SiteSettings.objects.get(pk=1).theme == {"light": {}, "dark": {}}


def test_a_preset_replaces_the_colours_and_default_clears_them(admin: Client) -> None:
    get_site_settings()

    assert (
        admin.post(settings_url(), settings_form(theme_preset="steel", **{"theme__bg__dark": "#000001"})).status_code
        == 302
    )
    theme = SiteSettings.objects.get(pk=1).theme
    assert theme["light"]["accent"] == "#2F6DB3"
    assert theme["dark"]["bg"] == "#0F141B"

    assert admin.post(settings_url(), settings_form(theme_preset="default")).status_code == 302
    assert SiteSettings.objects.get(pk=1).theme == {"light": {}, "dark": {}}


def test_poor_contrast_warns_but_still_saves(admin: Client) -> None:
    get_site_settings()

    response = admin.post(
        settings_url(),
        settings_form(**{"theme__text__light": "#EEEEEE", "theme__bg__light": "#FFFFFF"}),
        follow=True,
    )

    assert response.status_code == 200
    assert "Low contrast in light mode: body text on the page background" in response.content.decode()
    assert SiteSettings.objects.get(pk=1).theme["light"]["text"] == "#EEEEEE"


def test_good_contrast_gives_no_warning(admin: Client) -> None:
    get_site_settings()

    body = admin.post(settings_url(), settings_form(theme_preset="desert"), follow=True).content.decode()

    assert "Low contrast" not in body


def test_unknown_fonts_are_refused(admin: Client) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(heading_font="Comic Sans;}</style>"))

    assert response.status_code == 200
    assert "heading_font" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).heading_font == ""


def test_saving_branding_changes_the_page_etag(admin: Client, client: Client) -> None:
    """TD-28: pages are cached by the data version, so a branding save must bump it."""
    get_site_settings()
    first = Client().get("/")["ETag"]

    admin.post(settings_url(), settings_form(**{"theme__accent__light": "#112233"}))

    assert Client().get("/")["ETag"] != first


# --- logo ---


def upload(data: bytes, name: str = "logo.png", content_type: str = "image/png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def test_uploading_a_logo_stores_a_reencoded_copy_and_serves_it(admin: Client, media_root: Path) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png())))

    assert response.status_code == 302
    logo = SiteSettings.objects.get(pk=1).logo
    assert re.fullmatch(r"branding/logo-[0-9a-f]{16}\.png", logo)
    stored = media_root / logo
    assert stored.is_file()
    assert Image.open(stored).size == (120, 40)
    served = admin.get(f"/media/{logo}")
    assert served.status_code == 200
    assert served["X-Content-Type-Options"] == "nosniff"
    assert logo in admin.get(settings_url()).content.decode()  # the form previews it


def test_the_stored_logo_ignores_the_declared_name_and_type(admin: Client, media_root: Path) -> None:
    get_site_settings()
    evil_name = "../../evil.html"

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png(), evil_name, "text/html")))

    assert response.status_code == 302
    logo = SiteSettings.objects.get(pk=1).logo
    assert re.fullmatch(r"branding/logo-[0-9a-f]{16}\.png", logo)
    assert not (media_root.parent / "evil.html").exists()
    assert [p.name for p in (media_root / "branding").iterdir()] == [logo.removeprefix("branding/")]


@pytest.mark.parametrize(
    ("data", "name", "content_type"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "logo.svg", "image/svg+xml"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "logo.png", "image/png"),  # SVG renamed to .png
        (b"<html><script>alert(1)</script></html>", "logo.png", "image/png"),
        (b"not an image at all", "logo.png", "image/png"),
        (png()[:60], "logo.png", "image/png"),  # truncated
        (png() + b"\x00" * (2 * 1024 * 1024), "logo.png", "image/png"),  # over the size limit
    ],
    ids=["svg", "svg-as-png", "html", "text", "truncated", "oversized"],
)
def test_bad_logo_uploads_are_refused_and_change_nothing(
    admin: Client, media_root: Path, data: bytes, name: str, content_type: str
) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(data, name, content_type)))

    assert response.status_code == 200
    assert "logo_upload" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).logo == ""
    assert SiteSettings.objects.get(pk=1).site_title != "Korea Fighters"  # nothing else was saved either
    assert not media_root.exists() or not list(media_root.rglob("*.*"))
    assert current_data_version() == before


def test_replacing_the_logo_removes_the_old_file(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    get_site_settings()
    with django_capture_on_commit_callbacks(execute=True):
        admin.post(settings_url(), settings_form(logo_upload=upload(png(colour="red"))))
    first = SiteSettings.objects.get(pk=1).logo

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        admin.post(settings_url(), settings_form(logo_upload=upload(png(colour="blue"))))
    assert (media_root / first).exists()  # nothing is deleted before the new row is committed
    for callback in callbacks:
        callback()
    second = SiteSettings.objects.get(pk=1).logo

    assert first != second
    assert not (media_root / first).exists()
    assert (media_root / second).is_file()


def test_saving_without_a_new_upload_keeps_the_logo(admin: Client, media_root: Path) -> None:
    get_site_settings()
    admin.post(settings_url(), settings_form(logo_upload=upload(png())))
    logo = SiteSettings.objects.get(pk=1).logo

    admin.post(settings_url(), settings_form(site_title="Renamed"))

    row = SiteSettings.objects.get(pk=1)
    assert (row.logo, row.site_title) == (logo, "Renamed")
    assert (media_root / logo).is_file()


def test_removing_the_logo(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    get_site_settings()
    admin.post(settings_url(), settings_form(logo_upload=upload(png())))
    logo = SiteSettings.objects.get(pk=1).logo

    with django_capture_on_commit_callbacks(execute=True):
        response = admin.post(settings_url(), settings_form(remove_logo="on"))

    assert response.status_code == 302
    assert SiteSettings.objects.get(pk=1).logo == ""
    assert not (media_root / logo).exists()


def test_upload_and_remove_together_is_an_error(admin: Client, media_root: Path) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png()), remove_logo="on"))

    assert response.status_code == 200
    assert "remove_logo" in response.context["adminform"].form.errors


# --- custom fonts ---

VENDOR = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "vendor"


def font_file(name: str = "BarlowCondensed-700.woff2", upload_name: str = "My Font.woff2") -> SimpleUploadedFile:
    return SimpleUploadedFile(upload_name, (VENDOR / name).read_bytes(), content_type="font/woff2")


def upload_font(admin: Client, use: str = "", **extra: object) -> SiteSettings:
    admin.post(settings_url(), settings_form(font_upload=font_file(), font_use=use, **extra))
    return SiteSettings.objects.get(pk=1)


def test_uploading_a_font_stores_serves_and_selects_it(admin: Client, media_root: Path) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(font_upload=font_file(), font_use="both"))

    assert response.status_code == 302
    row = SiteSettings.objects.get(pk=1)
    [stored] = row.custom_fonts
    assert re.fullmatch(r"branding/font-[0-9a-f]{16}\.woff2", stored["file"])
    assert stored["label"] == "My Font"
    assert (media_root / stored["file"]).read_bytes() == (VENDOR / "BarlowCondensed-700.woff2").read_bytes()
    assert row.heading_font == row.body_font == f"up-{stored['file'][len('branding/font-') :][:8]}"
    assert current_data_version() != before  # cached pages are revalidated (TD-28)
    served = admin.get(f"/media/{stored['file']}")
    assert (served.status_code, served["Content-Type"]) == (200, "font/woff2")
    page = Client().get("/").content.decode()
    assert "@font-face" in page
    assert f"/media/{stored['file']}" in page
    assert "font-display:swap" in page


def test_the_form_previews_and_offers_the_uploaded_font(admin: Client, media_root: Path) -> None:
    get_site_settings()
    row = upload_font(admin)
    key = f"up-{row.custom_fonts[0]['file'][len('branding/font-') :][:8]}"

    form_page = admin.get(settings_url()).content.decode()

    assert "My Font" in form_page
    assert "The quick brown fox" in form_page  # the preview line
    assert "@font-face" in form_page
    assert f'value="{key}"' in form_page  # a choice for the heading font, the body font and the remove checkbox
    assert form_page.count(f'value="{key}"') == 3


def test_an_upload_without_choosing_a_use_does_not_select_it(admin: Client, media_root: Path) -> None:
    get_site_settings()

    row = upload_font(admin, use="")

    assert len(row.custom_fonts) == 1
    assert (row.heading_font, row.body_font) == ("", "")
    assert "@font-face" not in Client().get("/").content.decode()  # not used: nothing is emitted


def test_a_stored_font_can_be_selected_later(admin: Client, media_root: Path) -> None:
    get_site_settings()
    key = f"up-{upload_font(admin).custom_fonts[0]['file'][len('branding/font-') :][:8]}"

    admin.post(settings_url(), settings_form(heading_font=key, body_font="serif"))

    row = SiteSettings.objects.get(pk=1)
    assert (row.heading_font, row.body_font) == (key, "serif")
    assert row.custom_fonts  # saving without an upload keeps the list


def test_choosing_a_font_that_was_never_uploaded_is_refused(admin: Client, media_root: Path) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(heading_font="up-deadbeef"))

    assert response.status_code == 200
    assert "heading_font" in response.context["adminform"].form.errors


def test_removing_a_font_deletes_the_file_and_resets_the_choice(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    get_site_settings()
    with django_capture_on_commit_callbacks(execute=True):
        row = upload_font(admin, use="both")
    [stored] = row.custom_fonts
    key = row.heading_font

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = admin.post(settings_url(), settings_form(remove_fonts=[key], heading_font=key, body_font="serif"))
    assert response.status_code == 302
    assert (media_root / stored["file"]).exists()  # nothing is deleted before the row is committed
    for callback in callbacks:
        callback()

    row = SiteSettings.objects.get(pk=1)
    assert row.custom_fonts == []
    assert (row.heading_font, row.body_font) == ("", "serif")
    assert not (media_root / stored["file"]).exists()
    assert Client().get(f"/media/{stored['file']}").status_code == 404


def test_replacing_a_font_keeps_only_the_wanted_files(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    get_site_settings()
    with django_capture_on_commit_callbacks(execute=True):
        first = upload_font(admin, use="heading").custom_fonts[0]
    with django_capture_on_commit_callbacks(execute=True):
        admin.post(
            settings_url(),
            settings_form(
                font_upload=font_file("BarlowCondensed-600.woff2", "Other.woff2"),
                font_use="heading",
                remove_fonts=[f"up-{first['file'][len('branding/font-') :][:8]}"],
            ),
        )

    [second] = SiteSettings.objects.get(pk=1).custom_fonts
    assert second["label"] == "Other"
    assert not (media_root / first["file"]).exists()
    assert (media_root / second["file"]).is_file()


def test_uploading_the_same_font_twice_stores_it_once(admin: Client, media_root: Path) -> None:
    get_site_settings()
    upload_font(admin)

    row = upload_font(admin)

    assert len(row.custom_fonts) == 1
    assert len(list((media_root / "branding").glob("font-*"))) == 1


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><font/></svg>', "font.svg"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>' + b" " * 100, "font.woff2"),  # SVG renamed
        (b"wOF2" + b"\x00" * 100, "font.woff2"),  # right magic, wrong header
        ((VENDOR / "BarlowCondensed-700.woff2").read_bytes(), "font.woff"),  # WOFF2 under a WOFF name
        ((VENDOR / "BarlowCondensed-700.woff2").read_bytes()[:500], "font.woff2"),  # truncated
        (b"wOF2" + b"\x00" * (2 * 1024 * 1024), "font.woff2"),  # over the size limit
        (b"\x00\x01\x00\x00" + b"\x00" * 100, "font.ttf"),
    ],
    ids=["svg", "svg-as-woff2", "bad-header", "woff2-as-woff", "truncated", "oversized", "ttf"],
)
def test_bad_font_uploads_are_refused_and_change_nothing(
    admin: Client, media_root: Path, data: bytes, name: str
) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(
        settings_url(), settings_form(font_upload=SimpleUploadedFile(name, data, content_type="font/woff2"))
    )

    assert response.status_code == 200
    assert "font_upload" in response.context["adminform"].form.errors
    row = SiteSettings.objects.get(pk=1)
    assert row.custom_fonts == []
    assert row.site_title != "Korea Fighters"
    assert not media_root.exists() or not list(media_root.rglob("*.*"))
    assert current_data_version() == before


def test_the_number_of_uploaded_fonts_is_limited(admin: Client, media_root: Path) -> None:
    row = get_site_settings()
    row.custom_fonts = [{"file": f"branding/font-{i:016x}.woff2", "label": str(i)} for i in range(6)]
    row.save()

    response = admin.post(settings_url(), settings_form(font_upload=font_file()))

    assert response.status_code == 200
    assert "font_upload" in response.context["adminform"].form.errors
    assert len(SiteSettings.objects.get(pk=1).custom_fonts) == 6


def test_a_large_font_is_accepted_with_a_warning(admin: Client, media_root: Path) -> None:
    get_site_settings()
    raw = (VENDOR / "BarlowCondensed-700.woff2").read_bytes()
    size = 200 * 1024

    big = raw[:8] + struct.pack(">I", size) + raw[12:] + b"\x00" * (size - len(raw))

    response = admin.post(
        settings_url(),
        settings_form(font_upload=SimpleUploadedFile("Big.woff2", big, content_type="font/woff2")),
        follow=True,
    )

    assert len(SiteSettings.objects.get(pk=1).custom_fonts) == 1
    assert "200 KB" in response.content.decode()


# --- players and missions ---


def make_players_and_missions() -> tuple[Player, Player, Mission]:
    saved = save(mission((sortie(0, 1), sortie(1, 2))))
    first, second = Player.objects.order_by("pk")
    return first, second, saved


def test_players_are_listed_and_searchable_by_name_and_uuid(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    Player.objects.filter(pk=first.pk).update(current_name="Zanzibar", name_lower="zanzibar")

    assert admin.get("/admin/il2ks_db/player/").status_code == 200
    by_name = admin.get("/admin/il2ks_db/player/", {"q": "zanzib"}).context["cl"].result_list
    by_uuid = admin.get("/admin/il2ks_db/player/", {"q": second.account_uuid}).context["cl"].result_list
    assert list(by_name) == [first]
    assert list(by_uuid) == [second]


def test_players_are_searchable_by_an_old_name(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    PlayerName.objects.create(player=first, name="OldHandle", name_lower="oldhandle", first_seen=NOW, last_seen=NOW)

    found = admin.get("/admin/il2ks_db/player/", {"q": "oldhandle"}).context["cl"].result_list

    assert list(found) == [first]
    assert second not in found


def test_player_form_only_allows_hiding(admin: Client) -> None:
    first, _, _ = make_players_and_missions()
    url = f"/admin/il2ks_db/player/{first.pk}/change/"
    form = admin.get(url).context["adminform"].form
    assert list(form.fields) == ["is_hidden"]
    before = current_data_version()

    response = admin.post(url, {"is_hidden": "on", "current_name": "Hacked", "kills_air": "999", "elo_prop": "9"})

    assert response.status_code == 302
    first.refresh_from_db()
    assert first.is_hidden
    assert first.current_name != "Hacked"
    assert first.kills_air != 999
    assert current_data_version() > before


def test_players_and_missions_cannot_be_added_or_deleted(admin: Client) -> None:
    first, _, saved = make_players_and_missions()

    for model, obj in (("player", first), ("mission", saved)):
        assert admin.get(f"/admin/il2ks_db/{model}/add/").status_code == 403
        assert admin.get(f"/admin/il2ks_db/{model}/{obj.pk}/delete/").status_code == 403
        listing = admin.get(f"/admin/il2ks_db/{model}/").content.decode()
        assert "delete_selected" not in listing
    assert Player.objects.filter(pk=first.pk).exists()
    assert Mission.objects.filter(pk=saved.pk).exists()


def test_hide_and_unhide_actions_for_players(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    before = current_data_version()

    admin.post("/admin/il2ks_db/player/", {"action": "hide_selected", "_selected_action": [first.pk]})

    assert list(Player.objects.visible()) == [second]
    assert list(Player.objects.hidden()) == [first]
    hidden_version = current_data_version()
    assert hidden_version > before

    admin.post("/admin/il2ks_db/player/", {"action": "unhide_selected", "_selected_action": [first.pk, second.pk]})

    assert Player.objects.visible().count() == 2
    assert current_data_version() > hidden_version


def test_hide_and_unhide_actions_for_missions(admin: Client) -> None:
    _, _, saved = make_players_and_missions()
    before = current_data_version()

    admin.post("/admin/il2ks_db/mission/", {"action": "hide_selected", "_selected_action": [saved.pk]})

    assert Mission.objects.visible().count() == 0
    assert current_data_version() > before
    admin.post("/admin/il2ks_db/mission/", {"action": "unhide_selected", "_selected_action": [saved.pk]})
    assert Mission.objects.visible().count() == 1


def test_mission_search_and_form(admin: Client) -> None:
    _, _, saved = make_players_and_missions()
    url = f"/admin/il2ks_db/mission/{saved.pk}/change/"

    found = admin.get("/admin/il2ks_db/mission/", {"q": saved.mission_uid[:10]}).context["cl"].result_list
    nothing = admin.get("/admin/il2ks_db/mission/", {"q": "no-such-mission"}).context["cl"].result_list

    assert list(found) == [saved]
    assert list(nothing) == []
    assert list(admin.get(url).context["adminform"].form.fields) == ["is_hidden"]
    assert admin.post(url, {"is_hidden": "on", "players_total": "77"}).status_code == 302
    saved.refresh_from_db()
    assert saved.is_hidden
    assert saved.players_total != 77


def test_visible_managers_exclude_hidden_rows() -> None:
    first, second, saved = make_players_and_missions()
    Player.objects.filter(pk=first.pk).update(is_hidden=True)
    Mission.objects.filter(pk=saved.pk).update(is_hidden=True)

    assert list(Player.objects.visible()) == [second]
    assert Mission.objects.visible().count() == 0
    assert Player.objects.all().count() == 2
    assert list(Player.objects.filter(pk=second.pk).visible()) == [second]  # chainable on a queryset too


# --- game objects and countries ---


def test_game_object_admin_edits_only_the_display_name(admin: Client) -> None:
    obj = GameObject.objects.create(
        log_name="Mystery-1", display_name="Mystery-1", cls=ObjectClass.UNKNOWN, is_known=False
    )
    url = f"/admin/il2ks_db/gameobject/{obj.pk}/change/"
    assert list(admin.get(url).context["adminform"].form.fields) == ["display_name"]
    before = current_data_version()

    response = admin.post(url, {"display_name": "Mystery Jet", "cls": "fighter", "is_known": "on", "log_name": "x"})

    assert response.status_code == 302
    obj.refresh_from_db()
    assert obj.display_name == "Mystery Jet"
    assert (obj.log_name, obj.cls, obj.is_known) == ("Mystery-1", ObjectClass.UNKNOWN, False)
    assert current_data_version() > before
    assert admin.get("/admin/il2ks_db/gameobject/add/").status_code == 403


def test_game_object_filters_find_unknown_objects(admin: Client) -> None:
    GameObject.objects.create(log_name="Known-1", display_name="Known", cls=ObjectClass.FIGHTER, is_known=True)
    GameObject.objects.create(log_name="Unknown-1", display_name="Unknown-1", cls=ObjectClass.UNKNOWN, is_known=False)

    unknown = admin.get("/admin/il2ks_db/gameobject/", {"is_known__exact": "0"}).context["cl"].result_list
    fighters = admin.get("/admin/il2ks_db/gameobject/", {"cls__exact": "fighter"}).context["cl"].result_list
    searched = admin.get("/admin/il2ks_db/gameobject/", {"q": "unknown"}).context["cl"].result_list

    assert [o.log_name for o in unknown] == ["Unknown-1"]
    assert [o.log_name for o in fighters] == ["Known-1"]
    assert [o.log_name for o in searched] == ["Unknown-1"]


def test_an_edited_game_object_name_is_an_override_until_reset_or_typed_back(admin: Client) -> None:
    """TD-24: edits are overrides on top of the shipped (translated) defaults; the shipped name is no override."""
    default = load_default_catalog().lookup("F-86A-5").display_name
    obj = GameObject.objects.create(log_name="F-86A-5", display_name=default, cls=ObjectClass.FIGHTER)
    url = f"/admin/il2ks_db/gameobject/{obj.pk}/change/"

    admin.post(url, {"display_name": "My Sabre"})
    obj.refresh_from_db()
    assert (obj.display_name, obj.name_overridden) == ("My Sabre", True)

    admin.post(url, {"display_name": default})
    obj.refresh_from_db()
    assert obj.name_overridden is False

    admin.post(url, {"display_name": "My Sabre"})
    admin.post("/admin/il2ks_db/gameobject/", {"action": "reset_names", "_selected_action": [obj.pk]})
    obj.refresh_from_db()
    assert (obj.display_name, obj.name_overridden) == (default, False)


def test_game_object_names_can_be_reset_to_the_catalog(admin: Client) -> None:
    default = load_default_catalog().lookup("F-86A-5").display_name
    assert default
    obj = GameObject.objects.create(log_name="F-86A-5", display_name="My Sabre", cls=ObjectClass.FIGHTER)
    before = current_data_version()

    admin.post("/admin/il2ks_db/gameobject/", {"action": "reset_names", "_selected_action": [obj.pk]})

    obj.refresh_from_db()
    assert obj.display_name == default
    assert current_data_version() > before


def test_country_names_can_be_edited_and_reset(admin: Client) -> None:
    country = Country.objects.create(code=501, coalition=1, display_name="Home-made")
    url = f"/admin/il2ks_db/country/{country.pk}/change/"
    assert list(admin.get(url).context["adminform"].form.fields) == ["display_name"]
    before = current_data_version()

    assert admin.post(url, {"display_name": "Red Land", "code": "999"}).status_code == 302
    country.refresh_from_db()
    assert (country.display_name, country.code) == ("Red Land", 501)
    assert current_data_version() > before

    admin.post("/admin/il2ks_db/country/", {"action": "reset_names", "_selected_action": [country.pk]})
    country.refresh_from_db()
    assert country.display_name == load_default_catalog().country_name(501, 1)


# --- ingestion runs and the status page ---


def run(uid: str, status: str = IngestStatus.OK, *, minutes: int = 0, **fields: object) -> IngestRun:
    return IngestRun.objects.create(
        mission_uid=uid,
        fingerprint=f"fp-{uid}-{minutes}",
        status=status,
        started_at=NOW + timedelta(minutes=minutes),
        finished_at=NOW + timedelta(minutes=minutes, seconds=5),
        **fields,
    )


def test_ingest_runs_are_read_only(admin: Client) -> None:
    ingested = run(
        "2026-10-01_10-00-00", warnings=["something odd"], unknown_atypes={"99": 3}, unknown_keys={"3:ZZ": 2}
    )

    assert admin.get("/admin/il2ks_db/ingestrun/add/").status_code == 403
    assert admin.get(f"/admin/il2ks_db/ingestrun/{ingested.pk}/delete/").status_code == 403
    assert admin.post(f"/admin/il2ks_db/ingestrun/{ingested.pk}/change/", {"status": "failed"}).status_code == 403
    ingested.refresh_from_db()
    assert ingested.status == IngestStatus.OK


def test_ingest_run_detail_shows_warnings_unknowns_and_the_error(admin: Client) -> None:
    failed = run(
        "2026-10-01_10-00-00",
        IngestStatus.FAILED,
        warnings=["clock skew <b>detected</b>"],
        unknown_atypes={"99": 3},
        unknown_keys={"3:ZZ": 2},
        error="Traceback (most recent call last):\n  File x\nValueError: boom",
        files=["missionReport(2026-10-01_10-00-00)[0].txt"],
    )

    body = admin.get(f"/admin/il2ks_db/ingestrun/{failed.pk}/change/").content.decode()

    assert "ValueError: boom" in body
    assert "clock skew &lt;b&gt;detected&lt;/b&gt;" in body, "warnings are escaped"
    assert "3:ZZ" in body
    assert "99" in body
    assert "missionReport(2026-10-01_10-00-00)[0].txt" in body


def test_ingest_run_list_filters_and_search(admin: Client) -> None:
    run("2026-10-01_10-00-00", minutes=0)
    run("2026-10-02_10-00-00", IngestStatus.FAILED, minutes=1, completion_reason=CompletionReason.IDLE)

    def uids(**query: str) -> list[str]:
        response = admin.get("/admin/il2ks_db/ingestrun/", query)
        assert response.status_code == 200
        return [r.mission_uid for r in response.context["cl"].result_list]

    assert uids() == ["2026-10-02_10-00-00", "2026-10-01_10-00-00"]
    assert uids(status__exact="failed") == ["2026-10-02_10-00-00"]
    assert uids(completion_reason__exact="idle") == ["2026-10-02_10-00-00"]
    assert uids(q="10-01") == ["2026-10-01_10-00-00"]
    later = (NOW + timedelta(seconds=30)).isoformat()
    assert uids(started_at__gte=later) == ["2026-10-02_10-00-00"]


def test_status_page_when_nothing_was_ingested(admin: Client) -> None:
    response = admin.get("/admin/ingestion/")

    assert response.status_code == 200
    assert "Nothing ingested yet" in response.content.decode()


def test_status_page_sections(admin: Client) -> None:
    run("2026-10-01_10-00-00", minutes=0)
    run("2026-10-02_10-00-00", completion_reason=CompletionReason.IDLE, minutes=1, unknown_atypes={"99": 3})
    run("2026-10-02_11-00-00", minutes=2, unknown_atypes={"99": 2}, unknown_keys={"3:ZZ": 4})
    # waiting for a retry
    run(
        "2026-10-03_08-00-00",
        IngestStatus.FAILED,
        minutes=3,
        attempts=1,
        next_retry_at=NOW + timedelta(hours=1),
        error="E-RETRY",
    )
    # given up
    run("2026-10-03_09-00-00", IngestStatus.FAILED, minutes=4, attempts=4, next_retry_at=None, error="E-GAVE-UP")
    # failed first, then fixed: not listed
    run("2026-10-03_10-00-00", IngestStatus.FAILED, minutes=5, next_retry_at=NOW, error="E-FIXED")
    run("2026-10-03_10-00-00", IngestStatus.OK, minutes=6)
    GameObject.objects.create(
        log_name="Weird-Plane", display_name="Weird-Plane", cls=ObjectClass.UNKNOWN, is_known=False
    )
    GameObject.objects.create(log_name="Normal-Plane", display_name="Normal", cls=ObjectClass.FIGHTER, is_known=True)
    status = admin.get("/admin/ingestion/")
    body = status.content.decode()
    overview = status.context["overview"]

    assert status.status_code == 200
    assert overview.last_ok.mission_uid == "2026-10-03_10-00-00"
    assert [r.mission_uid for r in overview.waiting_retry] == ["2026-10-03_08-00-00"]
    assert [r.mission_uid for r in overview.gave_up] == ["2026-10-03_09-00-00"]
    assert "E-RETRY" in body
    assert "E-GAVE-UP" in body
    assert "E-FIXED" not in body
    assert "Weird-Plane" in body
    assert "Normal-Plane" not in body
    assert [(u.name, u.count, u.missions) for u in overview.unknown_atypes] == [("99", 5, 2)]
    assert [(u.name, u.count, u.missions) for u in overview.unknown_keys] == [("3:ZZ", 4, 1)]
    assert [r.mission_uid for r in overview.idle_completions] == ["2026-10-02_10-00-00"]
    assert "2026-10-02_10-00-00" in body
