"""The media view serves only `MEDIA_ROOT/branding/<file>` (FR-ADM-2, NFR-SEC-7)."""

import io
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest
from django.http import FileResponse
from django.test import Client
from PIL import Image
from pytest_django.fixtures import Settings

from il2ks.web.logo import process_logo, store_logo


@pytest.fixture
def media_root(tmp_path: Path, settings: Settings) -> Path:
    settings.MEDIA_ROOT = tmp_path
    settings.DEBUG = False
    return tmp_path


@pytest.fixture
def logo_name(media_root: Path) -> str:
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(out, format="PNG")
    logo = process_logo(out.getvalue())
    store_logo(logo, media_root)
    return logo.name


def test_serves_the_logo_with_safe_headers(client: Client, logo_name: str) -> None:
    response = client.get(f"/media/{logo_name}")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "max-age=31536000" in response["Cache-Control"]
    assert "immutable" in response["Cache-Control"]
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert response["Content-Disposition"] == "inline"
    assert isinstance(response, FileResponse)
    assert b"".join(cast("Iterable[bytes]", response.streaming_content)).startswith(b"\x89PNG")


def test_head_and_conditional_requests(client: Client, logo_name: str) -> None:
    first = client.get(f"/media/{logo_name}")
    etag = first["ETag"]

    head = client.head(f"/media/{logo_name}")
    again = client.get(f"/media/{logo_name}", headers={"If-None-Match": etag})

    assert head.status_code == 200
    assert again.status_code == 304
    assert again["X-Content-Type-Options"] == "nosniff"
    assert client.post(f"/media/{logo_name}").status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "branding/missing.png",
        "branding/",
        "branding",
        "branding/../secret.png",
        "branding/%2e%2e/secret.png",
        "branding/..%2fsecret.png",
        "branding/%2e%2e%2fsecret.png",
        "branding//secret.png",
        "branding/sub/secret.png",
        "branding\\secret.png",
        "branding/.hidden.png",
        "branding/secret.svg",
        "branding/secret.html",
        "branding/secret.txt",
        "branding/secret.png%00.png",
        "../secret.png",
        "other/secret.png",
        "secret.png",
    ],
)
def test_everything_else_is_a_404(client: Client, media_root: Path, path: str) -> None:
    (media_root / "secret.png").write_bytes(b"outside the branding folder")
    (media_root / "branding").mkdir(exist_ok=True)
    (media_root / "branding" / "secret.svg").write_text("<svg/>")
    (media_root / "branding" / "secret.html").write_text("<html/>")
    (media_root / "branding" / "secret.txt").write_text("text")
    (media_root / "branding" / ".hidden.png").write_bytes(b"hidden")

    assert client.get(f"/media/{path}").status_code == 404


def test_a_directory_named_like_an_image_is_a_404(client: Client, media_root: Path) -> None:
    (media_root / "branding" / "dir.png").mkdir(parents=True)

    assert client.get("/media/branding/dir.png").status_code == 404


def test_a_symlink_out_of_the_folder_is_a_404(client: Client, media_root: Path) -> None:
    secret = media_root / "secret.png"
    secret.write_bytes(b"outside")
    (media_root / "branding").mkdir()
    link = media_root / "branding" / "link.png"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")

    assert client.get("/media/branding/link.png").status_code == 404
