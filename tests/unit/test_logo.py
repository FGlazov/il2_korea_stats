"""Logo upload pipeline (FR-ADM-2, NFR-SEC-7): what is accepted, what is refused, what comes out."""

import io
import re
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from il2ks.web.logo import (
    MAX_HEIGHT,
    MAX_UPLOAD_BYTES,
    LogoError,
    delete_logo,
    process_logo,
    store_logo,
)

NAME = re.compile(r"branding/logo-[0-9a-f]{16}\.png")


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **options)
    return out.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_png_with_alpha_is_reencoded_and_keeps_alpha() -> None:
    source = Image.new("RGBA", (300, 100), (200, 30, 30, 0))
    source.putpixel((5, 5), (10, 20, 30, 255))

    logo = process_logo(encode(source, "PNG"))

    assert NAME.fullmatch(logo.name)
    result = decode(logo.data)
    assert result.format == "PNG"
    assert result.mode == "RGBA"
    assert result.getpixel((5, 5)) == (10, 20, 30, 255)
    transparent = result.getpixel((50, 50))
    assert isinstance(transparent, tuple)
    assert transparent[3] == 0


def test_same_pixels_give_the_same_name() -> None:
    data = encode(Image.new("RGB", (40, 40), "red"), "PNG")

    assert process_logo(data).name == process_logo(data).name
    assert process_logo(data).name != process_logo(encode(Image.new("RGB", (40, 40), "blue"), "PNG")).name


def test_jpeg_and_webp_are_accepted_and_become_png() -> None:
    image = Image.new("RGB", (64, 32), "green")

    for fmt in ("JPEG", "WEBP"):
        logo = process_logo(encode(image, fmt))
        assert decode(logo.data).format == "PNG"
        assert logo.name.endswith(".png")


def test_metadata_is_stripped() -> None:
    info = PngInfo()
    info.add_text("Comment", "secret-marker-123")
    png = encode(Image.new("RGB", (32, 32), "white"), "PNG", pnginfo=info)
    assert b"secret-marker-123" in png

    exif = Image.Exif()
    exif[0x010E] = "secret-marker-456"  # ImageDescription
    jpeg = encode(Image.new("RGB", (32, 32), "white"), "JPEG", exif=exif)
    assert b"secret-marker-456" in jpeg

    for upload in (png, jpeg):
        out = process_logo(upload).data
        assert b"secret-marker" not in out
        assert decode(out).info.keys() <= {"dpi", "gamma", "srgb"}


def test_trailing_bytes_after_the_image_are_dropped() -> None:
    """A polyglot (valid PNG followed by markup) decodes as an image but only the pixels survive."""
    png = encode(Image.new("RGB", (32, 32), "white"), "PNG")

    out = process_logo(png + b"<script>alert(1)</script><html>").data

    assert b"<script>" not in out
    assert b"<html>" not in out


def test_exif_orientation_is_applied() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 clockwise to display
    jpeg = encode(Image.new("RGB", (200, 100), "white"), "JPEG", exif=exif)

    logo = process_logo(jpeg)

    assert (logo.width, logo.height) == (100, 200)


def test_large_images_are_scaled_down_keeping_the_aspect_ratio() -> None:
    logo = process_logo(encode(Image.new("RGB", (4000, 2000), "white"), "PNG"))

    assert logo.height == MAX_HEIGHT
    assert logo.width == 2 * MAX_HEIGHT
    assert decode(logo.data).size == (logo.width, logo.height)


def test_small_images_are_not_enlarged() -> None:
    logo = process_logo(encode(Image.new("RGB", (30, 20), "white"), "PNG"))

    assert (logo.width, logo.height) == (30, 20)


def test_palette_with_transparency_and_cmyk_become_rgb_modes() -> None:
    palette = Image.new("P", (20, 20), 0)
    palette.putpalette([255, 0, 0] * 256)
    assert decode(process_logo(encode(palette, "PNG", transparency=0)).data).mode == "RGBA"
    assert decode(process_logo(encode(Image.new("CMYK", (20, 20)), "JPEG")).data).mode == "RGB"
    assert decode(process_logo(encode(Image.new("L", (20, 20)), "PNG")).data).mode == "RGB"


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(1)</script></svg>',
        b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
        b"<!doctype html><html><body><script>alert(1)</script></body></html>",
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
        b"%PDF-1.4 not an image",
        b"plain text",
        b"\x89PNG\r\n\x1a\n",  # a PNG signature and nothing else
    ],
    ids=["svg", "xml-svg", "html", "gif", "pdf", "text", "bare-signature"],
)
def test_non_images_and_other_formats_are_refused(payload: bytes) -> None:
    with pytest.raises(LogoError):
        process_logo(payload)


def test_html_wrapped_around_an_image_is_refused() -> None:
    png = encode(Image.new("RGB", (16, 16), "white"), "PNG")

    with pytest.raises(LogoError):
        process_logo(b"<html><body>" + png)


def test_bmp_is_refused_even_though_pillow_can_read_it() -> None:
    with pytest.raises(LogoError):
        process_logo(encode(Image.new("RGB", (16, 16), "white"), "BMP"))


def test_truncated_image_is_refused() -> None:
    png = encode(Image.effect_noise((200, 200), 80).convert("RGB"), "PNG")

    with pytest.raises(LogoError, match=r"damaged|truncated"):
        process_logo(png[: len(png) // 2])


def test_empty_upload_is_refused() -> None:
    with pytest.raises(LogoError):
        process_logo(b"")


def test_oversized_upload_is_refused_before_decoding() -> None:
    png = encode(Image.new("RGB", (16, 16), "white"), "PNG")

    with pytest.raises(LogoError, match="larger"):
        process_logo(png + b"\x00" * MAX_UPLOAD_BYTES)


def test_too_many_pixels_is_refused_from_the_header() -> None:
    """6000 x 5000 = 30 megapixels compresses to a few KB, so the byte limit alone doesn't stop it."""
    data = encode(Image.new("L", (6000, 5000)), "PNG")
    assert len(data) < MAX_UPLOAD_BYTES

    with pytest.raises(LogoError, match="pixels"):
        process_logo(data)


def test_decompression_bomb_is_refused() -> None:
    """Pillow itself raises above ~179 megapixels; that must also end as a `LogoError`."""
    data = encode(Image.new("1", (14000, 14000)), "PNG")
    assert len(data) < MAX_UPLOAD_BYTES

    with pytest.raises(LogoError, match="pixels"):
        process_logo(data)


def test_store_logo_writes_once_and_atomically(tmp_path: Path) -> None:
    logo = process_logo(encode(Image.new("RGB", (40, 40), "red"), "PNG"))

    path = store_logo(logo, tmp_path)
    assert path == tmp_path / logo.name
    assert path.read_bytes() == logo.data
    assert store_logo(logo, tmp_path) == path
    assert [p.name for p in (tmp_path / "branding").iterdir()] == [path.name]  # no temp files left


def test_delete_logo_only_touches_files_in_the_branding_folder(tmp_path: Path) -> None:
    logo = process_logo(encode(Image.new("RGB", (40, 40), "red"), "PNG"))
    stored = store_logo(logo, tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")

    delete_logo("../secret.png", tmp_path)
    delete_logo("secret.png", tmp_path)
    delete_logo("", tmp_path)
    assert outside.exists()

    delete_logo(logo.name, tmp_path)
    assert not stored.exists()
