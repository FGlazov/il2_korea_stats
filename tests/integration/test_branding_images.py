"""Admin uploads of the header/home background pictures and the tab icon (FR-ADM-2): the form, what is stored and
served, the rendered markup, removal, permission and CSRF, and the order of the settings page's sections."""

import io
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

from il2ks.db.models import SiteSettings
from il2ks.db.site import get_site_settings
from tests.integration import test_admin
from tests.integration.test_admin import png, settings_form, settings_url

pytestmark = pytest.mark.django_db
admin = test_admin.admin
media_root = test_admin.media_root

BG = re.compile(r"branding/bg-(home|header)-[0-9a-f]{16}\.webp")


@pytest.fixture(autouse=True)
def _row() -> None:
    get_site_settings()


def upload(data: bytes, name: str = "bg.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="image/png")


def row() -> SiteSettings:
    return SiteSettings.objects.get(pk=1)


def home_page(client: Client) -> str:
    return Client().get("/").content.decode()


def test_without_uploads_the_page_is_unchanged(client: Client) -> None:
    page = home_page(client)

    assert "hero--image" not in page
    assert "site-header--image" not in page
    assert "--il2-home-bg" not in page
    assert "img/brand/favicon.svg" in page
    assert "apple-touch-icon" not in page
    assert 'class="hero__mark' in page or "hero__mark" in page


@pytest.mark.parametrize("kind", ["home", "header"])
def test_uploading_a_background_stores_serves_and_renders_it(admin: Client, media_root: Path, kind: str) -> None:
    response = admin.post(
        settings_url(),
        settings_form(
            **{
                f"{kind}_bg_upload": upload(png(800, 300, "white")),
                f"{kind}_bg_position": "left",
                f"{kind}_bg_shade": "70",
            }
        ),
    )

    assert response.status_code == 302
    name = getattr(row(), f"{kind}_bg_image")
    assert BG.fullmatch(name)
    assert Image.open(media_root / name).format == "WEBP"
    served = admin.get(f"/media/{name}")
    assert served.status_code == 200
    assert "max-age=31536000" in served["Cache-Control"]
    assert served["Content-Type"] == "image/webp"
    page = home_page(admin)
    assert f'--il2-{kind}-bg:url("/media/{name}")' in page
    assert f"--il2-{kind}-bg-pos:left center" in page
    assert f"--il2-{kind}-bg-shade:70%" in page
    assert ("hero--image" in page) == (kind == "home")
    assert ("site-header--image" in page) == (kind == "header")
    assert ("hero__mark" in page) == (kind != "home")  # the built-in aircraft mark gives way to the picture


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
        (b"<html><script>alert(1)</script></html>", "html"),
        (b"plain text", "text"),
        (png(60, 60)[:50], "truncated"),
        (png() + b"\0" * (4 * 1024 * 1024), "too-big"),
    ],
    ids=["svg", "html", "text", "truncated", "too-big"],
)
def test_bad_background_uploads_are_refused(admin: Client, media_root: Path, data: bytes, why: str) -> None:
    response = admin.post(settings_url(), settings_form(home_bg_upload=upload(data)))

    assert response.status_code == 200, why
    assert "home_bg_upload" in response.context["adminform"].form.errors
    assert row().home_bg_image == ""
    assert not media_root.exists() or not list(media_root.rglob("*.*"))


def test_too_many_pixels_is_refused(admin: Client) -> None:
    out = io.BytesIO()
    Image.new("1", (6000, 5000)).save(out, format="PNG")

    response = admin.post(settings_url(), settings_form(header_bg_upload=upload(out.getvalue())))

    assert "pixels" in str(response.context["adminform"].form.errors["header_bg_upload"])
    assert row().header_bg_image == ""


def test_unknown_position_is_refused(admin: Client) -> None:
    response = admin.post(settings_url(), settings_form(home_bg_position="center;}</style><script>"))

    assert response.status_code == 200
    assert "home_bg_position" in response.context["adminform"].form.errors


@pytest.mark.parametrize("shade", ["-1", "81", "abc"])
def test_shade_outside_0_to_80_is_refused(admin: Client, shade: str) -> None:
    response = admin.post(settings_url(), settings_form(header_bg_shade=shade))

    assert response.status_code == 200
    assert "header_bg_shade" in response.context["adminform"].form.errors


@pytest.mark.parametrize("shade", ["0", "80"])
def test_shade_bounds_are_accepted(admin: Client, shade: str) -> None:
    assert admin.post(settings_url(), settings_form(header_bg_shade=shade)).status_code == 302
    assert row().header_bg_shade == int(shade)


def test_a_save_without_the_new_fields_keeps_the_stored_choice(admin: Client) -> None:
    admin.post(
        settings_url(), settings_form(home_bg_upload=upload(png()), home_bg_position="right", home_bg_shade="30")
    )

    admin.post(settings_url(), settings_form(site_title="Renamed"))

    assert (row().home_bg_position, row().home_bg_shade, bool(row().home_bg_image)) == ("right", 30, True)


def test_removing_a_background_restores_the_built_in_decoration(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    admin.post(settings_url(), settings_form(home_bg_upload=upload(png())))
    name = row().home_bg_image
    assert (media_root / name).is_file()

    with django_capture_on_commit_callbacks(execute=True):
        admin.post(settings_url(), settings_form(remove_home_bg="on"))

    assert row().home_bg_image == ""
    assert not (media_root / name).exists()
    assert "hero--image" not in home_page(admin)


def test_upload_and_remove_together_is_refused(admin: Client) -> None:
    response = admin.post(settings_url(), settings_form(home_bg_upload=upload(png()), remove_home_bg="on"))

    assert "remove_home_bg" in response.context["adminform"].form.errors


def test_replacing_a_background_removes_the_old_file(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        admin.post(settings_url(), settings_form(header_bg_upload=upload(png(colour="red"))))
    first = row().header_bg_image
    with django_capture_on_commit_callbacks(execute=True):
        admin.post(settings_url(), settings_form(header_bg_upload=upload(png(colour="blue"))))

    assert row().header_bg_image != first
    assert not (media_root / first).exists()


# --- permission and CSRF ---


def test_anonymous_cannot_post_the_settings(client: Client) -> None:
    response = client.post(settings_url(), settings_form(home_bg_upload=upload(png())))

    assert response.status_code == 302
    assert "/login" in response["Location"]
    assert row().home_bg_image == ""


def test_staff_without_change_permission_cannot_upload(client: Client) -> None:
    staff = User.objects.create_user("viewer", "v@example.org", "x", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_sitesettings"))
    client.force_login(staff)

    response = client.post(settings_url(), settings_form(home_bg_upload=upload(png())))

    assert response.status_code == 403
    assert row().home_bg_image == ""


def test_a_post_without_the_csrf_token_is_refused() -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(User.objects.create_superuser("boss", "b@example.org", "x"))

    response = client.post(settings_url(), settings_form(home_bg_upload=upload(png())))

    assert response.status_code == 403
    assert row().home_bg_image == ""


# --- tab icon ---


def test_a_logo_becomes_the_tab_icon_in_two_square_sizes(admin: Client, media_root: Path) -> None:
    admin.post(settings_url(), settings_form(logo_upload=upload(png(300, 80, "red"))))

    base = row().favicon
    assert re.fullmatch(r"branding/icon-[0-9a-f]{16}", base)
    for size in (32, 180):
        assert Image.open(media_root / f"{base}-{size}.png").size == (size, size)
    served = admin.get(f"/media/{base}-180.png")
    assert served.status_code == 200
    assert "immutable" in served["Cache-Control"]
    page = home_page(admin)
    assert f'<link rel="icon" href="/media/{base}-32.png"' in page
    assert f'<link rel="apple-touch-icon" href="/media/{base}-180.png"' in page
    assert "img/brand/favicon.svg" not in page


def test_a_separate_tab_icon_wins_over_the_logo(admin: Client) -> None:
    admin.post(settings_url(), settings_form(logo_upload=upload(png(300, 80, "red"))))
    admin.post(settings_url(), settings_form(favicon_upload=upload(png(64, 64, "blue"))))

    custom, derived = row().favicon_custom, row().favicon
    assert custom
    assert derived
    assert custom != derived
    assert f"/media/{custom}-32.png" in home_page(admin)

    admin.post(settings_url(), settings_form(remove_favicon="on"))
    assert row().favicon_custom == ""
    assert f"/media/{derived}-32.png" in home_page(admin)


def test_removing_the_logo_restores_the_built_in_tab_icon(
    admin: Client, media_root: Path, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    admin.post(settings_url(), settings_form(logo_upload=upload(png())))
    base = row().favicon

    with django_capture_on_commit_callbacks(execute=True):
        admin.post(settings_url(), settings_form(remove_logo="on"))
    assert not (media_root / f"{base}-32.png").exists()

    assert row().favicon == ""
    assert "img/brand/favicon.svg" in home_page(admin)


def test_a_bad_tab_icon_is_refused(admin: Client) -> None:
    response = admin.post(settings_url(), settings_form(favicon_upload=upload(b"nope")))

    assert "favicon_upload" in response.context["adminform"].form.errors


def test_a_hand_edited_icon_name_is_ignored(admin: Client) -> None:
    SiteSettings.objects.filter(pk=1).update(favicon='x"><script>alert(1)</script>')

    page = home_page(admin)

    assert "<script>alert(1)" not in page
    assert "img/brand/favicon.svg" in page


# --- the settings page's order ---


def test_navigation_links_help_sits_with_the_link_rows(admin: Client) -> None:
    page = admin.get(settings_url()).content.decode()

    help_at = page.index("Extra links for the top navigation bar")
    rows_at = (
        page.index("nav_links-TOTAL_FORMS")
        if "nav_links-TOTAL_FORMS" in page
        else page.index("navlink_set-TOTAL_FORMS")
    )
    heading_at = (
        page.index("<h2>Navigation links</h2>")
        if "<h2>Navigation links</h2>" in page
        else page.index("Navigation links")
    )
    assert heading_at < help_at < rows_at
    # nothing but the navigation links section lies between the help text and the rows
    between = page[help_at:rows_at]
    assert "Coalitions" not in between
    assert "Running mission" not in between
    assert "Logo and browser tab icon" not in between
    # and the sections come in a logical order: look first, behavior after, links last
    order = [
        page.index(t) for t in ("Logo and browser tab icon", "Header background", "Home banner background", "Colors")
    ]
    assert order == sorted(order)
    assert page.index("Coalitions") < heading_at
