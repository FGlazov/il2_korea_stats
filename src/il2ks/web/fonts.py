"""Custom font uploads: untrusted input, so only a clean WOFF2/WOFF file is kept (FR-ADM-2, NFR-SEC-7).

Like the logo (`web.logo`), the pipeline never trusts the file name or the declared content type:

1. the extension must be `.woff2` or `.woff` (never SVG, TTF/OTF, EOT or anything else)
2. size limit on the raw bytes
3. the magic number must match the extension (`wOF2` / `wOFF`), the header's length field must equal the real size
   (a truncated or padded file fails) and the sfnt flavor must be a known one (TrueType, CFF, collection)
4. the stored name is derived from the content hash (`branding/font-<sha256 prefix>.woff2`), so it is immutable and
   cacheable, and the CSS font family is generated from the same hash (`il2-font-<hash>`)

Nothing the admin typed ever reaches the CSS: the family name and the file name are built here from hex digits, the
display label (from the upload's file name) is only shown in HTML, escaped by the template engine. The font bytes are
served as they are; browsers sanitize fonts themselves (OTS), and the file is served with `nosniff` and a strict CSP.
"""

import hashlib
import re
import struct
from collections.abc import Collection
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext as _

from il2ks.web.logo import BRANDING_DIR

MAX_FONT_BYTES = 2 * 1024 * 1024
RECOMMENDED_FONT_BYTES = 150 * 1024  # above this the admin gets a (non-blocking) warning: every page load pays for it
MAX_CUSTOM_FONTS = 6
MAGIC = {".woff2": b"wOF2", ".woff": b"wOFF"}
FLAVORS = frozenset({0x00010000, 0x4F54544F, 0x74727565, 0x74746366})  # TrueType, 'OTTO' (CFF), 'true', 'ttcf'
FONT_FILE = re.compile(rf"{BRANDING_DIR}/font-[0-9a-f]{{16}}\.(?:woff2|woff)")
MEDIA_URL_OK = re.compile(r"/(?:[A-Za-z0-9_-]+/)*")
LABEL_MAX = 40
_LABEL_BAD = re.compile(r"[^\w .-]")


class FontError(ValueError):
    """The upload was refused; the message is shown to the admin."""


@dataclass(frozen=True, slots=True)
class CustomFont:
    """One uploaded font as stored in `SiteSettings.custom_fonts`."""

    file: str  # relative to MEDIA_ROOT, always `branding/font-<hash16>.(woff2|woff)`
    label: str  # shown to the admin only (HTML-escaped by the template engine), never put in CSS

    @property
    def digest(self) -> str:
        return self.file.removeprefix(f"{BRANDING_DIR}/font-").split(".")[0]

    @property
    def key(self) -> str:
        """The value stored in `heading_font` / `body_font` to select this font."""
        return f"up-{self.digest[:8]}"

    @property
    def family(self) -> str:
        """The generated CSS font family name."""
        return f"il2-font-{self.digest}"

    @property
    def format(self) -> str:
        return "woff2" if self.file.endswith(".woff2") else "woff"

    def as_json(self) -> dict[str, str]:
        return {"file": self.file, "label": self.label}


@dataclass(frozen=True, slots=True)
class ProcessedFont:
    data: bytes
    font: CustomFont


def _label(text: str) -> str:
    # Translators: the name shown for an uploaded font whose file name gave nothing usable (a noun phrase)
    return " ".join(_LABEL_BAD.sub(" ", text).split())[:LABEL_MAX].strip() or _("Custom font")


def process_font(raw: bytes, filename: str) -> ProcessedFont:
    """Validate an uploaded font. Raises `FontError` for anything that isn't a plausible WOFF2/WOFF file."""
    path = Path(filename.replace("\\", "/"))
    suffix = path.suffix.lower()
    if suffix not in MAGIC:
        raise FontError(_("Only .woff2 and .woff font files are accepted (no SVG, TTF, OTF or EOT)."))
    if not raw:
        raise FontError(_("The file is empty."))
    if len(raw) > MAX_FONT_BYTES:
        raise FontError(_("The file is larger than %(mb)d MB.") % {"mb": MAX_FONT_BYTES // (1024 * 1024)})
    if len(raw) < 48 or raw[:4] != MAGIC[suffix]:
        raise FontError(_("This is not a valid %(kind)s font file.") % {"kind": suffix[1:].upper()})
    flavor, length, tables = struct.unpack(">IIH", raw[4:14])
    if length != len(raw) or tables == 0 or flavor not in FLAVORS:
        raise FontError(_("The font file is damaged or truncated."))
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return ProcessedFont(raw, CustomFont(f"{BRANDING_DIR}/font-{digest}{suffix}", _label(path.stem)))


def clean_fonts(raw: object) -> list[CustomFont]:
    """The valid part of the stored list: well-formed entries only, no duplicates, at most `MAX_CUSTOM_FONTS`. Never
    raises (a hand-edited database row cannot inject anything)."""
    fonts: list[CustomFont] = []
    if not isinstance(raw, list):
        return fonts
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            continue
        file: object = item.get("file")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        label: object = item.get("label")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not (isinstance(file, str) and FONT_FILE.fullmatch(file)):
            continue
        font = CustomFont(file, _label(label if isinstance(label, str) else ""))
        if all(font.file != other.file for other in fonts) and len(fonts) < MAX_CUSTOM_FONTS:
            fonts.append(font)
    return fonts


def font_face_css(fonts: Collection[CustomFont], media_url: str = "/media/") -> str:
    """`@font-face` rules for the given (already validated) fonts: built from hex digits and fixed words only.

    One file serves every weight (`font-weight: 100 900`), so the browser never fakes a bold or light cut."""
    base = media_url if MEDIA_URL_OK.fullmatch(media_url) else "/media/"
    return "".join(
        f'@font-face{{font-family:"{f.family}";src:url("{base}{f.file}") format("{f.format}");'
        f"font-weight:100 900;font-style:normal;font-display:swap;}}"
        for f in fonts
    )


def prune_fonts(media_root: Path, keep: Collection[str]) -> None:
    """Remove every `branding/font-*` file whose name (relative to `media_root`) is not in `keep`.

    Runs after a successful save, like `prune_logos`: a removed font goes, and so does a file an earlier save could not
    delete (a file being streamed cannot be deleted on Windows) or left by a rolled-back upload. Never raises."""
    branding = media_root / BRANDING_DIR
    if not branding.is_dir():
        return
    kept = {Path(name).name for name in keep}
    for path in branding.glob("font-*"):
        if path.name not in kept and path.is_file():
            with suppress(OSError):
                path.unlink()
