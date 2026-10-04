"""The large front-page image (FR-ADM-2): validation like the logo, no original bytes served, change detection by
polling, the home page with the feature off and on, the admin form, the doctor check and cache invalidation (TD-28).
The configured file is never served itself."""

import io
import os
import re
import threading
from pathlib import Path

import pytest
from django.test import Client
from PIL import Image
from pytest_django.fixtures import Settings

from il2ks.db.models import HomeFeature, SiteSettings
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.ops import checks
from il2ks.ops.doctor import Level
from il2ks.web import feature_image
from il2ks.web.feature_image import FeatureImageError, inspect_source, process_feature, start_poller, sync
from tests.integration import test_admin
from tests.integration.test_admin import settings_form, settings_url
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db
admin = test_admin.admin  # the staff-login fixture of the admin tests

SECRET = b"TOP-SECRET-METADATA-MARKER"


@pytest.fixture(autouse=True)
def _settings_row() -> None:
    get_site_settings()  # the admin edits an existing row


@pytest.fixture
def media_root(tmp_path: Path, settings: Settings) -> Path:
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.DEBUG = False
    return tmp_path / "media"


def image_bytes(width: int = 3000, height: int = 1500, colour: str = "green", fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "elsewhere" / "map.png"
    path.parent.mkdir()
    path.write_bytes(image_bytes() + SECRET)  # trailing junk that must not survive
    return path


def enable(path: Path | str, **fields: object) -> SiteSettings:
    row = SiteSettings.objects.get_or_create(pk=1)[0]
    row.home_feature = HomeFeature.IMAGE
    row.feature_image_path = str(path)
    row.feature_alt = "Map of the front line"
    row.feature_caption = "Situation"
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


def touch_later(path: Path, seconds: int = 5) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 10**9))


# --- validation ---------------------------------------------------------------------------------------------------


def test_a_path_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FeatureImageError, match="no file"):
        inspect_source(str(tmp_path / "nope.png"))


def test_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FeatureImageError, match="folder"):
        inspect_source(str(tmp_path))


def test_an_empty_path_is_refused() -> None:
    with pytest.raises(FeatureImageError):
        inspect_source("  ")


@pytest.mark.parametrize(
    "content", [b"secret_key=abc", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"", b"\x89PNG\r\n"]
)
def test_a_non_image_is_refused_without_quoting_it(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "x.png"
    path.write_bytes(content)
    with pytest.raises(FeatureImageError) as caught:
        process_feature(inspect_source(str(path)))
    assert "secret_key" not in str(caught.value)


def test_a_file_over_the_size_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feature_image, "MAX_SOURCE_BYTES", 1000)
    path = tmp_path / "big.png"
    path.write_bytes(image_bytes(400, 400, "red"))
    with pytest.raises(FeatureImageError, match="larger"):
        process_feature(inspect_source(str(path)))


def test_too_many_pixels_are_refused_before_decoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feature_image, "MAX_SOURCE_PIXELS", 10_000)
    path = tmp_path / "wide.png"
    path.write_bytes(image_bytes(200, 100))
    with pytest.raises(FeatureImageError, match="too many pixels"):
        process_feature(inspect_source(str(path)))


def test_a_truncated_image_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cut.png"
    path.write_bytes(image_bytes()[:200])
    with pytest.raises(FeatureImageError):
        process_feature(inspect_source(str(path)))


def test_the_output_is_a_scaled_webp_without_any_original_bytes(source_file: Path) -> None:
    result = process_feature(inspect_source(str(source_file)))
    assert result.full.startswith(b"RIFF")
    assert result.full[8:12] == b"WEBP"
    assert (result.width, result.height) == (2560, 1280)  # 3000 x 1500 scaled down to the maximum width
    assert result.small is not None
    assert Image.open(io.BytesIO(result.small)).width == 960
    assert SECRET not in result.full + result.small
    assert source_file.read_bytes() not in result.full
    assert re.fullmatch(r"branding/feature-[0-9a-f]{16}\.webp", result.full_name)
    assert re.fullmatch(r"branding/feature-[0-9a-f]{16}-s\.webp", result.small_name)


def test_a_small_image_is_not_enlarged_and_has_no_phone_variant(tmp_path: Path) -> None:
    path = tmp_path / "small.jpg"
    path.write_bytes(image_bytes(600, 300, "blue", "JPEG"))
    result = process_feature(inspect_source(str(path)))
    assert (result.width, result.height) == (600, 300)
    assert result.small is None
    assert result.small_name == ""


# --- change detection ---------------------------------------------------------------------------------------------


def test_sync_does_nothing_while_the_feature_is_off(source_file: Path, media_root: Path) -> None:
    SiteSettings.objects.get_or_create(pk=1, defaults={"feature_image_path": str(source_file)})
    assert sync().changed is False
    assert SiteSettings.objects.get(pk=1).feature_image == ""


def test_sync_picks_up_a_new_and_a_modified_file(source_file: Path, media_root: Path) -> None:
    enable(source_file)
    version = current_data_version()

    first = sync()
    row = SiteSettings.objects.get(pk=1)
    assert first.changed
    assert first.error == ""
    assert (media_root / row.feature_image).is_file()
    assert (media_root / row.feature_image_small).is_file()
    assert current_data_version() == version + 1  # cached pages are invalidated (TD-28)
    assert row.feature_image_updated is not None

    unchanged = sync()  # same path, mtime and size: nothing to do
    assert not unchanged.changed
    assert current_data_version() == version + 1

    source_file.write_bytes(image_bytes(3000, 1500, "red"))
    touch_later(source_file)
    second = sync()
    new_row = SiteSettings.objects.get(pk=1)
    assert second.changed
    assert new_row.feature_image != row.feature_image
    assert current_data_version() == version + 2
    assert not (media_root / row.feature_image).exists()  # the old copy is removed
    assert (media_root / new_row.feature_image).is_file()


def test_a_broken_replacement_keeps_the_last_good_image_and_reports_the_problem(
    source_file: Path, media_root: Path
) -> None:
    enable(source_file)
    sync()
    good = SiteSettings.objects.get(pk=1).feature_image
    source_file.write_bytes(b"half written")
    touch_later(source_file)
    result = sync()
    row = SiteSettings.objects.get(pk=1)
    assert not result.changed
    assert result.error
    assert row.feature_image == good
    assert row.feature_error == result.error
    version = current_data_version()
    assert sync().error == result.error  # not retried while the file stays the same
    assert current_data_version() == version
    source_file.write_bytes(image_bytes(3000, 1500, "yellow"))
    touch_later(source_file, 10)
    assert sync().changed
    assert SiteSettings.objects.get(pk=1).feature_error == ""


def test_a_missing_file_is_an_error_not_a_crash(tmp_path: Path, media_root: Path) -> None:
    enable(tmp_path / "gone.png")
    result = sync()
    assert result.error
    assert not result.changed
    assert SiteSettings.objects.get(pk=1).feature_error == result.error


def test_the_poller_thread_calls_sync_repeatedly() -> None:
    called = threading.Event()

    def fake_sync() -> feature_image.SyncResult:
        called.set()
        return feature_image.SyncResult(False, "")

    thread = start_poller(0.01, sync_fn=fake_sync)
    assert thread is not None
    assert thread.daemon
    assert called.wait(5)
    assert start_poller(0.01) is None  # one thread per process


# --- the home page ------------------------------------------------------------------------------------------------


def test_the_home_page_has_no_feature_by_default(client: Client, media_root: Path) -> None:
    assert "home-feature" not in client.get("/").content.decode()


def test_the_home_page_shows_the_image_first(client: Client, source_file: Path, media_root: Path) -> None:
    enable(source_file)
    sync()
    html = client.get("/").content.decode()
    row = SiteSettings.objects.get(pk=1)
    assert html.index("home-feature") < html.index('class="hero"')
    assert f'src="/media/{row.feature_image}"' in html
    assert f"/media/{row.feature_image_small} 960w" in html
    assert 'alt="Map of the front line"' in html
    assert "Situation" in html
    assert "Updated" in html
    assert "data-il2-time" in html
    assert str(source_file) not in html  # the configured path stays private
    media = client.get(f"/media/{row.feature_image}")
    assert media.status_code == 200
    assert media["Content-Type"] == "image/webp"
    assert media["X-Content-Type-Options"] == "nosniff"


def test_the_feature_is_hidden_without_alt_text_or_a_picture(
    client: Client, source_file: Path, media_root: Path
) -> None:
    enable(source_file, feature_alt="")
    sync()
    assert "home-feature" not in client.get("/").content.decode()
    enable(source_file, feature_alt="Map", feature_image="")
    assert "home-feature" not in client.get("/").content.decode()


def test_a_new_image_changes_the_page_etag(client: Client, source_file: Path, media_root: Path) -> None:
    enable(source_file)
    sync()
    before = client.get("/")["ETag"]
    source_file.write_bytes(image_bytes(3000, 1500, "red"))
    touch_later(source_file)
    sync()
    assert client.get("/")["ETag"] != before


# --- the admin ----------------------------------------------------------------------------------------------------


def feature_form(path: Path | str, **overrides: object) -> dict[str, object]:
    return settings_form(
        home_feature="image", feature_image_path=str(path), feature_alt="Map", feature_caption="Now", **overrides
    )


def test_the_admin_saves_a_valid_image_and_shows_it(admin: Client, source_file: Path, media_root: Path) -> None:
    response = admin.post(settings_url(), feature_form(source_file))
    assert response.status_code == 302, response.content.decode()[:2000]
    row = SiteSettings.objects.get(pk=1)
    assert row.home_feature == "image"
    assert (media_root / row.feature_image).is_file()
    assert row.feature_error == ""
    assert row.feature_source_sig
    assert row.feature_image in admin.get(settings_url()).content.decode()  # the status preview


@pytest.mark.parametrize("what", ["missing", "directory", "text"])
def test_the_admin_refuses_a_bad_path(admin: Client, tmp_path: Path, media_root: Path, what: str) -> None:
    path = {"missing": tmp_path / "nope.png", "directory": tmp_path, "text": tmp_path / "t.txt"}[what]
    if what == "text":
        path.write_text("secret_key = 1")
    response = admin.post(settings_url(), feature_form(path))
    assert response.status_code == 200
    assert response.context["adminform"].form.errors["feature_image_path"]
    assert "secret_key" not in response.content.decode()
    assert SiteSettings.objects.get(pk=1).home_feature == "none"


def test_the_admin_requires_alt_text_and_a_path(admin: Client, media_root: Path) -> None:
    response = admin.post(settings_url(), settings_form(home_feature="image"))
    errors = response.context["adminform"].form.errors
    assert "feature_image_path" in errors
    assert "feature_alt" in errors


def test_switching_it_off_leaves_the_home_page_normal(
    admin: Client,
    client: Client,
    source_file: Path,
    media_root: Path,
) -> None:
    admin.post(settings_url(), feature_form(source_file))
    assert "home-feature" in client.get("/").content.decode()
    admin.post(settings_url(), settings_form(home_feature="none"))
    assert "home-feature" not in client.get("/").content.decode()


# --- il2ks doctor -------------------------------------------------------------------------------------------------


def test_doctor_is_silent_while_the_feature_is_off(tmp_path: Path) -> None:
    assert list(checks.front_page_image_check(make_instance(tmp_path))) == []


def test_doctor_reports_a_bad_path_as_an_error(tmp_path: Path) -> None:
    enable(tmp_path / "nope.png")
    findings = list(checks.front_page_image_check(make_instance(tmp_path)))
    assert [f.level for f in findings] == [Level.ERROR]
    assert "NT SERVICE" in findings[0].fix


def test_doctor_is_ok_for_a_good_file_and_warns_before_it_is_published(
    source_file: Path, tmp_path: Path, media_root: Path
) -> None:
    enable(source_file)
    cfg = make_instance(tmp_path)
    assert [f.level for f in checks.front_page_image_check(cfg)] == [Level.WARN]
    sync()
    assert [f.level for f in checks.front_page_image_check(cfg)] == [Level.OK]
