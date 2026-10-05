"""Optional branding pictures: the home banner and header backgrounds, and the browser-tab icon (FR-ADM-2, NFR-SEC-7).

Same trust model as the logo (`web.logo`): size, content-detected format and pixel limits, a full decode, and a fresh
re-encode from the pixels alone (no metadata), stored under `branding/` with a content-hash name. Nothing the admin
types reaches CSS or HTML unchecked: a background's position comes from the `POSITIONS` whitelist, its shade is an int
clamped to 0..80, and every URL is built here from a stored name that must match a strict pattern again at read time
(a hand-edited row simply shows nothing).

- Backgrounds: re-encoded as WebP (photos stay small), scaled down to `BACKGROUND_MAX`. `background_css` emits one
  `:root{...}` rule with custom properties that `site.css` uses for `.hero--image` and `.site-header--image`.
- Icons: a square, transparent-padded rendition of the logo (or of an explicitly uploaded icon) at 32 and 180 px, for
  `<link rel="icon">` and `apple-touch-icon`.
"""

import hashlib
import io
import re
from collections.abc import Collection
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from django.utils.safestring import SafeString, mark_safe
from PIL import Image

from il2ks.web.logo import BRANDING_DIR, LogoError, decode_raster

type BackgroundKind = Literal["home", "header"]

MAX_BACKGROUND_BYTES = 4 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 25_000_000
BACKGROUND_MAX = (2560, 1200)  # bounding box; only ever shrinks
WEBP_QUALITY = 82

POSITIONS: Final[dict[str, str]] = {  # key (stored) -> CSS `background-position`; the only values that reach CSS
    "center": "center center",
    "left": "left center",
    "right": "right center",
    "top": "center top",
    "bottom": "center bottom",
}
MAX_SHADE = 80

ICON_SIZES: Final[tuple[int, ...]] = (32, 180)
_BG_NAME = re.compile(r"branding/bg-(home|header)-[0-9a-f]{16}\.webp")
_ICON_BASE = re.compile(r"branding/icon-[0-9a-f]{16}")
_CSS_ALLOWED = re.compile(r'[A-Za-z0-9:{};()\-_/." %]+')


@dataclass(frozen=True, slots=True)
class ProcessedBackground:
    data: bytes
    name: str  # `branding/bg-<kind>-<hash>.webp`


@dataclass(frozen=True, slots=True)
class ProcessedIcons:
    base: str  # `branding/icon-<hash>`; the files are `<base>-<size>.png`
    files: dict[str, bytes]  # name relative to MEDIA_ROOT -> PNG bytes


def process_background(raw: bytes, kind: BackgroundKind) -> ProcessedBackground:
    """Validate and re-encode an uploaded background. Raises `LogoError` for anything that isn't a clean raster."""
    pixels = decode_raster(raw, max_bytes=MAX_BACKGROUND_BYTES, max_pixels=MAX_BACKGROUND_PIXELS)
    pixels.thumbnail(BACKGROUND_MAX, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    pixels.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)  # no exif is passed: no metadata is written
    data = out.getvalue()
    return ProcessedBackground(data, f"{BRANDING_DIR}/bg-{kind}-{hashlib.sha256(data).hexdigest()[:16]}.webp")


def process_icons(raw: bytes, *, max_bytes: int, max_pixels: int) -> ProcessedIcons:
    """Square renditions (transparent padding, never cropped; empty transparent margins are trimmed first)."""
    source = decode_raster(raw, max_bytes=max_bytes, max_pixels=max_pixels).convert("RGBA")
    box = source.getchannel("A").getbbox()
    if box is None:
        raise LogoError("The image is fully transparent.")
    source = source.crop(box)
    side = max(source.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(source, ((side - source.width) // 2, (side - source.height) // 2))
    renditions: dict[int, bytes] = {}
    for size in ICON_SIZES:
        out = io.BytesIO()
        scaled = square.resize((size, size), Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]
        scaled.save(out, format="PNG", optimize=True)
        renditions[size] = out.getvalue()
    base = f"{BRANDING_DIR}/icon-{hashlib.sha256(renditions[ICON_SIZES[-1]]).hexdigest()[:16]}"
    return ProcessedIcons(base, {f"{base}-{size}.png": data for size, data in renditions.items()})


def has_background(name: str, kind: BackgroundKind) -> bool:
    """Is `name` a stored background of this kind (the strict pattern; '' and hand-edited values are not)?"""
    return bool(_BG_NAME.fullmatch(name)) and f"bg-{kind}-" in name


def background_css(media_url: str, home: tuple[str, str, int], header: tuple[str, str, int]) -> SafeString:
    """The `:root{...}` custom properties for the stored backgrounds (each `(name, position key, shade)`), '' when
    neither is usable. Only validated pieces are interpolated."""
    declarations: list[str] = []
    entries: tuple[tuple[BackgroundKind, tuple[str, str, int]], ...] = (("home", home), ("header", header))
    for kind, (name, position, shade) in entries:
        if not has_background(name, kind):
            continue
        css_position = POSITIONS.get(position, POSITIONS["center"])
        percent = max(0, min(MAX_SHADE, int(shade)))
        declarations.append(
            f'--il2-{kind}-bg:url("{media_url}{name}");--il2-{kind}-bg-pos:{css_position};'
            f"--il2-{kind}-bg-shade:{percent}%;"
        )
    if not declarations:
        return mark_safe("")
    css = ":root{" + "".join(declarations) + "}"
    if not _CSS_ALLOWED.fullmatch(css):
        return mark_safe("")
    return mark_safe(css)


def prune_branding_pictures(media_root: Path, keep: Collection[str]) -> None:
    """Remove every `branding/bg-*` and `branding/icon-*` file whose name is not in `keep` (names relative to
    `media_root`). Runs after a committed save, like `prune_logos`; never raises (a file in use is tried next time)."""
    branding = media_root / BRANDING_DIR
    if not branding.is_dir():
        return
    kept = {Path(name).name for name in keep if name}
    for pattern in ("bg-*", "icon-*"):
        for path in branding.glob(pattern):
            if path.name not in kept and path.is_file():
                with suppress(OSError):
                    path.unlink()


def icon_urls(media_url: str, custom: str, derived: str) -> tuple[str, str] | None:
    """(32 px icon URL, 180 px apple-touch-icon URL) of the uploaded tab icon, else the logo's, else None (the built-in
    favicon stays)."""
    for base in (custom, derived):
        if _ICON_BASE.fullmatch(base):
            return f"{media_url}{base}-32.png", f"{media_url}{base}-180.png"
    return None
