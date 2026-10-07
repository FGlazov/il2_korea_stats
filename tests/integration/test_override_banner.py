"""The red warning banner for out-of-date `custom/` overrides (TD-25): staff only, admin only, only with problems."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from pytest_django.fixtures import Settings

from il2ks.serving import custom, templateversions
from il2ks.serving.templateversions import Version, with_header

pytestmark = pytest.mark.django_db

BANNER = "il2ks-override-warning"
KEY = "templates/il2ks/base.html"
ADMIN_PAGES = ["/admin/", "/admin/il2ks_db/player/", "/admin/ingestion/", "/admin/auth/user/"]


@pytest.fixture
def custom_root(tmp_path: Path, settings: Settings) -> Iterator[Path]:
    root = tmp_path / "custom"
    settings.CUSTOM_DIR = root
    custom.startup_scan.cache_clear()
    yield root
    custom.startup_scan.cache_clear()


def place(root: Path, version: Version) -> None:
    path = root / KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(with_header("<p>my copy</p>\n", KEY, version), encoding="utf-8")


def builtin_version() -> Version:
    original = custom.builtin_file("templates", "il2ks/base.html")
    assert original is not None
    version = templateversions.version_of(original)
    assert version is not None
    return version


def old_version() -> Version:
    """An older N than the built-in file's."""
    return Version(builtin_version().major - 1, 0)


@pytest.fixture
def staff(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_staff_see_the_banner_on_every_admin_page(staff: Client, custom_root: Path, url: str) -> None:
    place(custom_root, old_version())

    response = staff.get(url)
    body = response.content.decode()

    assert response.status_code == 200
    assert BANNER in body
    assert KEY in body
    assert "il2ks custom diff templates/il2ks/base.html" in body
    assert f"version {old_version()}" in body


def test_no_banner_when_every_override_is_current(staff: Client, custom_root: Path) -> None:
    place(custom_root, builtin_version())
    assert BANNER not in staff.get("/admin/").content.decode()


def test_no_banner_when_an_override_is_only_behind_by_a_minor_version(
    staff: Client, custom_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cosmetic change (higher M, same N) never shows the banner (TD-25); a higher N does (the tests above)."""
    built = builtin_version()
    place(custom_root, Version(built.major, 0))

    def newer(_path: Path) -> Version:
        return Version(built.major, built.minor + 2)

    monkeypatch.setattr(templateversions, "version_of", newer)
    assert BANNER not in staff.get("/admin/").content.decode()
    assert [c.state for c in custom.scan(custom_root)] == ["behind"]


def test_no_banner_without_any_overrides(staff: Client, custom_root: Path) -> None:
    assert BANNER not in staff.get("/admin/").content.decode()


def test_files_that_replace_nothing_do_not_trigger_the_banner(staff: Client, custom_root: Path) -> None:
    path = custom_root / "static" / "my-banner.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"png")
    assert BANNER not in staff.get("/admin/").content.decode()


def test_the_login_page_never_shows_it(client: Client, custom_root: Path) -> None:
    place(custom_root, Version(0))
    response = client.get("/admin/login/")
    assert response.status_code == 200
    assert BANNER not in response.content.decode()


def test_public_pages_never_show_it_not_even_to_staff(staff: Client, client: Client, custom_root: Path) -> None:
    place(custom_root, Version(0))
    for url in ("/", "/404-does-not-exist/"):
        assert BANNER not in staff.get(url).content.decode()
        assert BANNER not in Client().get(url).content.decode()


def test_the_scan_runs_once_per_process_not_per_request(
    staff: Client, custom_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    place(custom_root, Version(0))
    calls: list[Path] = []
    real_scan = custom.scan

    def counting_scan(root: Path, records: dict[str, dict[str, str]] | None = None) -> list[custom.OverrideCheck]:
        calls.append(root)
        return real_scan(root, records)

    monkeypatch.setattr(custom, "scan", counting_scan)
    for _ in range(3):
        assert BANNER in staff.get("/admin/").content.decode()
    assert len(calls) == 1
