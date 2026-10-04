"""Logo uploads: untrusted input, so only clean pixels are kept (FR-ADM-2, NFR-SEC-7).

The pipeline never trusts the file name, the declared content type or the file's own claims:

1. size limit on the raw bytes
2. the format is detected from the content by Pillow and must be PNG, JPEG or WebP (never SVG, HTML, GIF, ...)
3. pixel limit on the header's dimensions, checked before any pixel is decoded (decompression bomb guard)
4. the image is fully decoded (a truncated or corrupt file fails here), rotated by its EXIF orientation
5. it is re-encoded as a fresh PNG from the pixels alone: metadata, trailing bytes and embedded payloads are gone, the
   alpha channel is kept, and the image is scaled down to a sane size

The stored name is derived from the content hash (`logo-<sha256 prefix>.png`), so it is immutable and cacheable.
"""

import hashlib
import io
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 25_000_000  # width * height of the upload; a 5000 x 5000 logo is already absurd
MAX_WIDTH = 1024
MAX_HEIGHT = 256  # the header shows the logo small; this keeps retina displays sharp
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")
BRANDING_DIR = "branding"  # under MEDIA_ROOT: the only folder the media view serves


class LogoError(ValueError):
    """The upload was refused; the message is shown to the admin."""


@dataclass(frozen=True, slots=True)
class ProcessedLogo:
    data: bytes
    name: str  # relative to MEDIA_ROOT, always `branding/logo-<hash>.png`
    width: int
    height: int


def process_logo(raw: bytes) -> ProcessedLogo:
    """Validate and re-encode an uploaded logo. Raises `LogoError` for anything that isn't a clean raster image."""
    if not raw:
        raise LogoError(_("The file is empty."))
    if len(raw) > MAX_UPLOAD_BYTES:
        raise LogoError(_("The file is larger than %(mb)d MB.") % {"mb": MAX_UPLOAD_BYTES // (1024 * 1024)})
    try:
        image = Image.open(io.BytesIO(raw), formats=ALLOWED_FORMATS)
    except Image.DecompressionBombError as exc:
        raise _too_many_pixels() from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise LogoError(_("This is not a PNG, JPEG or WebP image (SVG is not accepted).")) from exc
    with image:
        if image.width * image.height > MAX_PIXELS:
            raise _too_many_pixels()
        try:
            image.seek(0)  # animated PNG/WebP: first frame only
            image.load()
            upright = ImageOps.exif_transpose(image)
        except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError) as exc:
            raise LogoError(_("The image is damaged or truncated.")) from exc
        pixels = _normalize_mode(upright)
    pixels.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)  # only ever shrinks
    out = io.BytesIO()
    pixels.save(out, format="PNG", optimize=True)  # no exif/pnginfo is passed, so no metadata is written
    data = out.getvalue()
    digest = hashlib.sha256(data).hexdigest()[:16]
    return ProcessedLogo(data, f"{BRANDING_DIR}/logo-{digest}.png", pixels.width, pixels.height)


def _too_many_pixels() -> LogoError:
    return LogoError(_("The image has too many pixels (limit: %(px)d megapixels).") % {"px": MAX_PIXELS // 10**6})


def _normalize_mode(image: Image.Image) -> Image.Image:
    has_alpha = image.mode in {"RGBA", "LA", "PA", "La", "RGBa"} or "transparency" in image.info
    return image.convert("RGBA" if has_alpha else "RGB")


def store_logo(logo: ProcessedLogo, media_root: Path) -> Path:
    """Write the logo under `media_root` (atomically; nothing to do if the same content is already there)."""
    return store_bytes(logo.data, logo.name, media_root)


def store_bytes(data: bytes, name: str, media_root: Path) -> Path:
    """Write `data` to `media_root / name` atomically (temp file, then rename); nothing to do if it is already there.
    Shared by the logo and the custom fonts (`web.fonts`); `name` is always derived from the content hash."""
    target = media_root / name
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def prune_logos(media_root: Path, keep: str) -> None:
    """Remove every `branding/logo-*` file except the current logo `keep` (its name relative to `media_root`, or "").

    Runs after a successful save: the replaced logo goes, and so does any file an earlier save could not delete (a file
    being streamed cannot be deleted on Windows) or left behind by a rolled-back upload. Never raises: a file that is
    still in use is simply tried again at the next save."""
    branding = media_root / BRANDING_DIR
    if not branding.is_dir():
        return
    kept = Path(keep).name if keep else ""
    for path in branding.glob("logo-*"):
        if path.name != kept and path.is_file():
            with suppress(OSError):
                path.unlink()
