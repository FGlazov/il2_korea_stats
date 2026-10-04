"""Custom font uploads (FR-ADM-2, NFR-SEC-7): what is accepted, what is refused, and that no CSS can be injected."""

import re
import struct
from pathlib import Path

import pytest

from il2ks.web.fonts import (
    MAX_CUSTOM_FONTS,
    MAX_FONT_BYTES,
    CustomFont,
    FontError,
    clean_fonts,
    font_face_css,
    process_font,
    prune_fonts,
)
from il2ks.web.logo import store_bytes
from il2ks.web.theme import theme_css

VENDOR = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "vendor"
NAME = re.compile(r"branding/font-[0-9a-f]{16}\.woff2?")


def fake(magic: bytes = b"wOF2", size: int = 200, flavor: int = 0x00010000, tables: int = 5, length: int = -1) -> bytes:
    """A file with a correct header and zero padding: all the checks look at (the browser would reject its tables)."""
    header = magic + struct.pack(">IIH", flavor, size if length < 0 else length, tables)
    return header + b"\x00" * (size - len(header))


def test_a_real_woff2_is_accepted() -> None:
    raw = (VENDOR / "BarlowCondensed-700.woff2").read_bytes()

    result = process_font(raw, "Barlow Condensed (bold).woff2")

    assert result.data == raw
    assert NAME.fullmatch(result.font.file)
    assert result.font.label == "Barlow Condensed bold"
    assert result.font.family == f"il2-font-{result.font.digest}"
    assert result.font.key == f"up-{result.font.digest[:8]}"
    assert len(result.font.key) <= 20  # fits SiteSettings.heading_font


def test_a_woff_is_accepted_and_stored_with_its_own_extension() -> None:
    result = process_font(fake(b"wOFF"), "Old.WOFF")

    assert result.font.file.endswith(".woff")
    assert result.font.format == "woff"


def test_the_stored_name_depends_on_the_content_only() -> None:
    first = process_font(fake(size=200), "a.woff2")
    again = process_font(fake(size=200), "../../something else.woff2")
    other = process_font(fake(size=300), "a.woff2")

    assert first.font.file == again.font.file
    assert first.font.file != other.font.file


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><font/></svg>", "font.svg"),
        (fake(), "font.ttf"),
        (fake(), "font.otf"),
        (fake(), "font.eot"),
        (fake(), "font"),
        (fake(), "font.woff2.html"),
        (fake(), "font.woff2.exe"),
        (fake(), ""),
    ],
    ids=["svg", "ttf", "otf", "eot", "no-extension", "html", "exe", "no-name"],
)
def test_wrong_extension_is_refused(raw: bytes, name: str) -> None:
    with pytest.raises(FontError, match="woff"):
        process_font(raw, name)


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        (fake(b"wOFF"), "x.woff2"),  # WOFF1 magic under a WOFF2 name
        (fake(b"wOF2"), "x.woff"),
        (b"\x00\x01\x00\x00" + b"\x00" * 200, "x.woff2"),  # a TTF renamed
        (b"<svg><script>alert(1)</script></svg>" + b" " * 100, "x.woff2"),  # SVG renamed
        (fake()[:20], "x.woff2"),  # far too short
    ],
    ids=["woff-as-woff2", "woff2-as-woff", "ttf-renamed", "svg-renamed", "tiny"],
)
def test_bad_magic_is_refused(raw: bytes, name: str) -> None:
    with pytest.raises(FontError, match="not a valid"):
        process_font(raw, name)


@pytest.mark.parametrize(
    "raw",
    [fake(length=999), fake()[:150], fake() + b"\x00" * 10, fake(tables=0), fake(flavor=0x12345678)],
    ids=["length-field-lies", "truncated", "trailing-bytes", "no-tables", "unknown-flavor"],
)
def test_damaged_files_are_refused(raw: bytes) -> None:
    with pytest.raises(FontError, match="damaged"):
        process_font(raw, "x.woff2")


def test_empty_and_oversized_files_are_refused() -> None:
    with pytest.raises(FontError, match="empty"):
        process_font(b"", "x.woff2")
    with pytest.raises(FontError, match="larger than 2 MB"):
        process_font(fake(size=MAX_FONT_BYTES + 1), "x.woff2")
    assert process_font(fake(size=MAX_FONT_BYTES), "x.woff2")  # exactly at the limit is fine


@pytest.mark.parametrize(
    ("filename", "label"),
    [
        ("Fira Sans.woff2", "Fira Sans"),
        ('x"};body{display:none}.woff2', "x body display none"),
        ("<script>alert(1)<_script>.woff2", "script alert 1 _script"),
        ('x"};body{display:none}/*.woff2', "Custom font"),  # a path: only the last part counts
        ("C:\\fonts\\Inter-Bold.woff2", "Inter-Bold"),
        ("***.woff2", "Custom font"),
        ("a" * 200 + ".woff2", "a" * 40),
        ("Шрифт.woff2", "Шрифт"),
    ],
)
def test_the_label_is_a_plain_short_text(filename: str, label: str) -> None:
    assert process_font(fake(), filename).font.label == label


# --- the stored list is re-validated on every read ---------------------------------------------------------

GOOD = {"file": "branding/font-0123456789abcdef.woff2", "label": "Good"}


@pytest.mark.parametrize(
    "entry",
    [
        {"file": "branding/font-0123456789abcdef.svg", "label": "x"},
        {"file": "branding/font-0123456789ABCDEF.woff2", "label": "x"},
        {"file": 'branding/font-0123456789abcdef.woff2"); } body { x: y', "label": "x"},
        {"file": "branding/../font-0123456789abcdef.woff2", "label": "x"},
        {"file": "//evil.example/font-0123456789abcdef.woff2", "label": "x"},
        {"file": "https://evil.example/f.woff2", "label": "x"},
        {"file": 5, "label": "x"},
        {"label": "x"},
        "branding/font-0123456789abcdef.woff2",
        None,
    ],
)
def test_malformed_entries_are_dropped(entry: object) -> None:
    assert clean_fonts([entry, GOOD]) == [CustomFont(GOOD["file"], "Good")]


def test_the_list_is_deduplicated_and_capped() -> None:
    many = [{"file": f"branding/font-{i:016x}.woff2", "label": str(i)} for i in range(MAX_CUSTOM_FONTS + 5)]

    assert len(clean_fonts(many)) == MAX_CUSTOM_FONTS
    assert len(clean_fonts([GOOD, GOOD])) == 1
    assert clean_fonts("not a list") == []
    assert clean_fonts({"file": GOOD["file"]}) == []


def test_a_stored_label_with_css_or_html_is_neutralised() -> None:
    fonts = clean_fonts([{"file": GOOD["file"], "label": '"};</style><script>x</script>'}])

    assert fonts[0].label == "style script x script"


# --- CSS output ------------------------------------------------------------------------------------------------------


def test_font_face_css_is_built_from_generated_values_only() -> None:
    css = font_face_css(clean_fonts([GOOD]), "/media/")

    assert css == (
        '@font-face{font-family:"il2-font-0123456789abcdef";src:url("/media/branding/font-0123456789abcdef.woff2") '
        'format("woff2");font-weight:100 900;font-style:normal;font-display:swap;}'
    )


@pytest.mark.parametrize("media_url", ['/m"); } x{', "https://evil.example/", "//evil.example/", "/a b/", "", "/media"])
def test_a_strange_media_url_falls_back(media_url: str) -> None:
    assert 'url("/media/branding/font-0123456789abcdef.woff2")' in font_face_css(clean_fonts([GOOD]), media_url)


def test_theme_css_uses_an_uploaded_font_for_headings_and_body() -> None:
    font = clean_fonts([GOOD])[0]

    css = theme_css({}, font.key, font.key, [GOOD])

    assert css.count("@font-face") == 1  # one rule, even when used twice
    assert "font-display:swap" in css
    assert f'--il2-font-display:"{font.family}", "Barlow Condensed"' in css  # falls back to the shipped look
    assert f'--pico-font-family:"{font.family}", system-ui' in css
    assert css.startswith("@font-face")
    assert css.endswith("}")


def test_theme_css_declares_only_the_fonts_in_use() -> None:
    other = {"file": "branding/font-fedcba9876543210.woff", "label": "Other"}
    fonts = [GOOD, other]

    css = theme_css({}, clean_fonts(fonts)[1].key, "", fonts)

    assert "fedcba9876543210.woff" in css
    assert "0123456789abcdef" not in css
    assert "--pico-font-family" not in css
    assert 'format("woff")' in css


def test_a_selected_font_that_is_not_in_the_list_is_ignored() -> None:
    assert theme_css({}, "up-01234567", "up-01234567", []) == ""
    assert theme_css({}, "up-01234567", "", [{"file": "x"}]) == ""


@pytest.mark.parametrize(
    "hostile",
    [
        [{"file": 'branding/font-0123456789abcdef.woff2"); } *{display:none} @font-face{src:url("x', "label": "x"}],
        [{"file": "branding/font-0123456789abcdef.woff2", "label": 'x"; } *{display:none}'}],
        "}</style><script>alert(1)</script>",
        {"file": GOOD["file"]},
    ],
)
def test_no_css_can_be_injected_through_the_stored_list(hostile: object) -> None:
    css = theme_css({}, "up-01234567", "up-01234567", hostile)

    assert "display:none" not in css
    assert "<" not in css
    assert css.count("{") == css.count("}")
    assert css in {"", theme_css({}, "up-01234567", "up-01234567", [])} or css.startswith("@font-face")


# --- storing and pruning ---------------------------------------------------------------------------------------------


def test_store_and_prune(tmp_path: Path) -> None:
    keep = process_font(fake(size=200), "keep.woff2").font
    drop = process_font(fake(size=300), "drop.woff2").font
    for font, size in ((keep, 200), (drop, 300)):
        store_bytes(fake(size=size), font.file, tmp_path)
    logo = tmp_path / "branding" / "logo-0123456789abcdef.png"
    logo.write_bytes(b"png")
    other = tmp_path / "branding" / "note.txt"
    other.write_text("not ours")

    prune_fonts(tmp_path, [keep.file])

    assert (tmp_path / keep.file).is_file()
    assert not (tmp_path / drop.file).exists()
    assert logo.is_file()  # the logo pruning is separate (`prune_logos`)
    assert other.is_file()


def test_prune_never_raises_for_a_file_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows refuses to delete a file that is being streamed; the next save tries again."""
    font = process_font(fake(), "x.woff2").font
    store_bytes(fake(), font.file, tmp_path)

    def locked(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(Path, "unlink", locked)

    prune_fonts(tmp_path, [])  # no exception

    assert (tmp_path / font.file).is_file()


def test_prune_without_a_folder_does_nothing(tmp_path: Path) -> None:
    prune_fonts(tmp_path, [])
