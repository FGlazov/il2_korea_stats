"""Background pictures and tab icons (FR-ADM-2, NFR-SEC-7): validation, re-encoding, square padding, safe CSS."""

import io
import re

import pytest
from PIL import Image

from il2ks.web.branding_images import (
    BACKGROUND_MAX,
    MAX_BACKGROUND_BYTES,
    POSITIONS,
    background_css,
    has_background,
    icon_urls,
    process_background,
    process_icons,
)
from il2ks.web.logo import LogoError


def encode(image: Image.Image, fmt: str = "PNG", **options: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **options)
    return out.getvalue()


def alpha(image: Image.Image, xy: tuple[int, int]) -> int:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    return pixel[3]


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_a_valid_background_is_reencoded_as_webp_with_a_hash_name() -> None:
    result = process_background(encode(Image.new("RGB", (800, 300), "white")), "home")

    assert re.fullmatch(r"branding/bg-home-[0-9a-f]{16}\.webp", result.name)
    assert decode(result.data).format == "WEBP"
    assert process_background(encode(Image.new("RGB", (800, 300), "white")), "header").name.startswith(
        "branding/bg-header-"
    )


def test_background_metadata_is_stripped() -> None:
    exif = Image.Exif()
    exif[0x010E] = "secret-marker-789"
    jpeg = encode(Image.new("RGB", (64, 64), "white"), "JPEG", exif=exif)
    assert b"secret-marker-789" in jpeg

    assert b"secret-marker" not in process_background(jpeg, "home").data


def test_large_backgrounds_are_scaled_down_and_small_ones_kept() -> None:
    big = decode(process_background(encode(Image.new("RGB", (5000, 1000), "white")), "header").data)
    small = decode(process_background(encode(Image.new("RGB", (300, 100), "white")), "header").data)

    assert big.width == BACKGROUND_MAX[0]
    assert small.size == (300, 100)


@pytest.mark.parametrize(
    "payload",
    [b"", b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"/>', b"<html><script>1</script></html>"],
)
def test_other_content_is_refused(payload: bytes) -> None:
    with pytest.raises(LogoError):
        process_background(payload, "home")


def test_gif_is_refused_even_though_pillow_reads_it() -> None:
    with pytest.raises(LogoError):
        process_background(encode(Image.new("RGB", (10, 10)), "GIF"), "home")


def test_oversized_upload_is_refused() -> None:
    with pytest.raises(LogoError, match="MB"):
        process_background(encode(Image.new("RGB", (10, 10))) + b"\0" * MAX_BACKGROUND_BYTES, "home")


def test_too_many_pixels_is_refused_from_the_header() -> None:
    with pytest.raises(LogoError, match="pixels"):
        process_background(encode(Image.new("1", (6000, 5000))), "home")


def test_icons_are_square_padded_not_cropped() -> None:
    wide = Image.new("RGBA", (400, 100), (255, 0, 0, 255))

    icons = process_icons(encode(wide), max_bytes=10**6, max_pixels=10**7)

    assert re.fullmatch(r"branding/icon-[0-9a-f]{16}", icons.base)
    assert sorted(icons.files) == [f"{icons.base}-180.png", f"{icons.base}-32.png"]
    big = decode(icons.files[f"{icons.base}-180.png"])
    assert big.size == (180, 180)
    assert big.mode == "RGBA"
    assert big.getpixel((90, 90)) == (255, 0, 0, 255)  # the logo is in the middle, whole
    assert alpha(big, (2, 90)) == 255  # full width used (not cropped): the left edge is still logo
    assert alpha(big, (90, 5)) == 0  # padding above and below is transparent
    assert decode(icons.files[f"{icons.base}-32.png"]).size == (32, 32)


def test_transparent_margins_are_trimmed_before_squaring() -> None:
    padded = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    padded.paste(Image.new("RGBA", (100, 100), (0, 0, 255, 255)), (250, 250))

    icons = process_icons(encode(padded), max_bytes=10**6, max_pixels=10**7)

    assert decode(icons.files[f"{icons.base}-32.png"]).getpixel((1, 1)) == (0, 0, 255, 255)


def test_fully_transparent_image_gives_no_icon() -> None:
    with pytest.raises(LogoError):
        process_icons(encode(Image.new("RGBA", (50, 50), (0, 0, 0, 0))), max_bytes=10**6, max_pixels=10**7)


HOME = "branding/bg-home-0123456789abcdef.webp"
HEADER = "branding/bg-header-0123456789abcdef.webp"


def test_css_has_only_whitelisted_positions_and_a_clamped_shade() -> None:
    css = background_css("/media/", (HOME, "left", 55), (HEADER, "evil;}</style>", 999))

    assert '--il2-home-bg:url("/media/branding/bg-home-0123456789abcdef.webp")' in css
    assert "--il2-home-bg-pos:left center" in css
    assert "--il2-home-bg-shade:55%" in css
    assert "--il2-header-bg-pos:center center" in css  # an unknown key falls back
    assert "--il2-header-bg-shade:80%" in css  # clamped
    assert "<" not in css
    assert "evil" not in css


def test_a_hand_edited_name_shows_nothing() -> None:
    assert background_css("/media/", ("branding/../../x.webp", "center", 10), ("", "center", 10)) == ""
    assert background_css("/media/", (HEADER, "center", 10), ("", "center", 10)) == ""  # wrong kind
    assert not has_background('x");}body{display:none', "home")


def test_position_keys_match_the_model_choices() -> None:
    from il2ks.db.models import BackgroundPosition

    assert set(POSITIONS) == set(BackgroundPosition.values)


def test_icon_urls_prefer_the_uploaded_icon_then_the_logo() -> None:
    custom, derived = "branding/icon-aaaaaaaaaaaaaaaa", "branding/icon-bbbbbbbbbbbbbbbb"

    assert icon_urls("/media/", custom, derived) == (f"/media/{custom}-32.png", f"/media/{custom}-180.png")
    assert icon_urls("/media/", "", derived) == (f"/media/{derived}-32.png", f"/media/{derived}-180.png")
    assert icon_urls("/media/", "", "") is None
    assert icon_urls("/media/", "../../etc", "x") is None
