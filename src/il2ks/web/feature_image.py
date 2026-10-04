"""The front-page feature image: a large picture (typically a map of the current situation) read from a file on the
server and shown at the top of the home page (FR-ADM-2, `SiteSettings.home_feature`).

**The configured file is never served.** An admin-typed path must not become a way to read arbitrary files (the secret
key, the database), so the file is treated like an upload: its content must be a PNG/JPEG/WebP
(`web.logo.decode_raster`: size and pixel limits, full decode, upright), and what is served is a fresh WebP encoded
from the pixels alone, stored as `media/branding/feature-<hash>.webp` (+ `-s` for phones)
and delivered by the media view with `nosniff`, a sandboxing
CSP and an immutable cache (the name carries the content hash, so caching and ETags stay correct, TD-28).

**Change detection is polling** (`sync`): the source's `(path, mtime_ns, size)` is compared with the signature stored
from the last look. It works the same on Windows and Linux and on SMB/NFS shares, where OS file notifications do not.
`start_poller` runs `sync` every `POLL_INTERVAL_S` seconds in a daemon thread of the web process (started by
`il2ks.wsgi`), so it works however the site runs (`run`, `web`, the Windows service, Docker) and no request ever writes
(TD-22). A new image bumps the data version (TD-28). The admin form validates once on save and hands the result to
`store_result`, so a bad path is refused with a clear message instead of being saved.

A source that later breaks (half-written, replaced by something else) never takes the page down: the last good image
stays, `feature_error` says what is wrong (admin page, `il2ks doctor`) and the file is looked at again when it changes.
"""

import hashlib
import io
import logging
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.utils.translation import gettext as _
from PIL import Image

from il2ks.db.models import HomeFeature, SiteSettings
from il2ks.db.site import bump_data_version
from il2ks.web.logo import BRANDING_DIR, LogoError, decode_raster, store_bytes

log = logging.getLogger(__name__)

MAX_SOURCE_BYTES: Final = 40 * 1024 * 1024  # a big map as PNG is a few MB; this is far beyond that
MAX_SOURCE_PIXELS: Final = 64_000_000  # e.g. 8000 x 8000; checked from the header, before any pixel is decoded
FULL_WIDTH: Final = 2560  # wider sources are scaled down (a retina display of a 1280 px container stays sharp)
SMALL_WIDTH: Final = 960  # the phone variant (`srcset`); sources not wider than this have none
WEBP_QUALITY: Final = 85
POLL_INTERVAL_S: Final = 5.0  # the file is noticed within about this long (+ the re-encode time)
PREFIX: Final = "feature-"
MISSING: Final = "missing"


class FeatureImageError(ValueError):
    """The configured file cannot be used; the message is shown to the admin (it never quotes the file's content)."""


@dataclass(frozen=True, slots=True)
class ProcessedFeature:
    full: bytes
    full_name: str  # relative to MEDIA_ROOT: `branding/feature-<hash>.webp`
    small: bytes | None
    small_name: str  # '' when there is no phone variant
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Source:
    """The state of the configured file at one moment: `signature` changes whenever its path, mtime or size does."""

    path: Path
    signature: str
    modified: datetime | None


@dataclass(frozen=True, slots=True)
class HomeFeatureView:
    """What `home.html` needs (`home_feature` in the template context). Plain data: no query, no file access."""

    url: str
    small_url: str  # '' = no phone variant
    width: int
    height: int
    small_width: int
    caption: str
    alt: str
    updated: datetime | None


def resolve_path(text: str) -> Path:
    """The configured path; a relative one is relative to the data folder. `~` is not expanded (nothing surprising)."""
    path = Path(text.strip())
    return path if path.is_absolute() else Path(settings.DATA_DIR) / path


def inspect_source(text: str) -> Source:
    """Look at the configured path (cheap: one `stat`). Raises `FeatureImageError` when it is not a readable file."""
    if not text.strip():
        raise FeatureImageError(_("No file location is set."))
    path = resolve_path(text)
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise FeatureImageError(_("There is no file at this location (the server could not find it).")) from exc
    except PermissionError as exc:
        raise FeatureImageError(_unreadable()) from exc
    except OSError as exc:
        raise FeatureImageError(
            _("The location cannot be reached (is the network share or drive available?).")
        ) from exc
    if path.is_dir():
        raise FeatureImageError(_("This location is a folder; enter the path of the image file itself."))
    if not path.is_file():
        raise FeatureImageError(_("This location is not a regular file."))
    modified = datetime.fromtimestamp(info.st_mtime, UTC)
    return Source(path, f"{path}|{info.st_mtime_ns}|{info.st_size}", modified)


def _unreadable() -> str:
    return _(
        "The server is not allowed to read this file. Give the account that runs il2ks read access to the file "
        "(Windows service: NT SERVICE\\il2ks; Docker: mount the file into the container)."
    )


def process_feature(source: Source) -> ProcessedFeature:
    """Read, validate and re-encode the source. Raises `FeatureImageError` for anything but a clean raster image."""
    try:
        size = source.path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise FeatureImageError(_("The file is larger than %(mb)d MB.") % {"mb": MAX_SOURCE_BYTES // (1024 * 1024)})
        with source.path.open("rb") as handle:
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    except PermissionError as exc:
        raise FeatureImageError(_unreadable()) from exc
    except OSError as exc:
        raise FeatureImageError(_("The file could not be read (is the network share or drive available?).")) from exc
    try:
        pixels = decode_raster(raw, max_bytes=MAX_SOURCE_BYTES, max_pixels=MAX_SOURCE_PIXELS)
    except LogoError as exc:
        raise FeatureImageError(str(exc)) from exc
    pixels.thumbnail((FULL_WIDTH, FULL_WIDTH * 4), Image.Resampling.LANCZOS)  # limits the width, only ever shrinks
    full = _webp(pixels)
    digest = hashlib.sha256(full).hexdigest()[:16]
    small: bytes | None = None
    small_name = ""
    if pixels.width > SMALL_WIDTH:
        phone = pixels.copy()
        phone.thumbnail((SMALL_WIDTH, SMALL_WIDTH * 4), Image.Resampling.LANCZOS)
        small = _webp(phone)
        small_name = f"{BRANDING_DIR}/{PREFIX}{digest}-s.webp"
    return ProcessedFeature(
        full, f"{BRANDING_DIR}/{PREFIX}{digest}.webp", small, small_name, pixels.width, pixels.height
    )


def _webp(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)  # no exif/xmp is passed: no metadata is written
    return out.getvalue()


def store_result(result: ProcessedFeature, media_root: Path) -> None:
    store_bytes(result.full, result.full_name, media_root)
    if result.small is not None:
        store_bytes(result.small, result.small_name, media_root)


def prune_feature_files(media_root: Path, keep: tuple[str, ...]) -> None:
    """Remove every `branding/feature-*` file that is not one of `keep` (names relative to `media_root`). Never raises:
    a file still being streamed cannot be deleted on Windows and is tried again at the next change."""
    branding = media_root / BRANDING_DIR
    if not branding.is_dir():
        return
    kept = {Path(name).name for name in keep if name}
    for path in branding.glob(f"{PREFIX}*"):
        if path.name not in kept and path.is_file():
            with suppress(OSError):
                path.unlink()


def media_url(name: str) -> str:
    return f"{settings.MEDIA_URL or '/media/'}{name}"


def home_feature_view(row: SiteSettings) -> HomeFeatureView | None:
    """The feature for the home page, or None when it is off, has no usable image yet or has no alt text."""
    if row.home_feature != HomeFeature.IMAGE.value or not row.feature_image or not row.feature_alt.strip():
        return None
    return HomeFeatureView(
        url=media_url(row.feature_image),
        small_url=media_url(row.feature_image_small) if row.feature_image_small else "",
        width=row.feature_image_width,
        height=row.feature_image_height,
        small_width=SMALL_WIDTH,
        caption=row.feature_caption.strip(),
        alt=row.feature_alt.strip(),
        updated=row.feature_image_updated,
    )


@dataclass(frozen=True, slots=True)
class SyncResult:
    changed: bool  # a new image was stored
    error: str  # '' = fine


def sync(*, force: bool = False) -> SyncResult:
    """Compare the source file with what was last looked at and, when it differs, produce the new image.

    Called by the polling thread and the admin save; safe to call from several processes at once (the names are content
    hashes, and the row is only updated if it still names the same path)."""
    row = SiteSettings.objects.filter(pk=1).first()
    if row is None or row.home_feature != HomeFeature.IMAGE.value or not row.feature_image_path.strip():
        return SyncResult(False, "")
    path_text = row.feature_image_path
    try:
        source = inspect_source(path_text)
    except FeatureImageError as exc:
        signature = f"{MISSING}|{path_text}|{exc}"[:700]
        if signature != row.feature_source_sig or force:
            _record_error(path_text, signature, str(exc))
        return SyncResult(False, str(exc))
    if not force and source.signature == row.feature_source_sig:
        return SyncResult(False, row.feature_error)
    try:
        result = process_feature(source)
    except FeatureImageError as exc:
        _record_error(path_text, source.signature, str(exc))
        return SyncResult(False, str(exc))
    store_result(result, Path(settings.MEDIA_ROOT))
    changed = _record_success(path_text, source, result)
    return SyncResult(changed, "")


def _record_error(path_text: str, signature: str, message: str) -> None:
    SiteSettings.objects.filter(pk=1, feature_image_path=path_text).update(
        feature_source_sig=signature, feature_error=message[:300]
    )
    log.warning("front-page image: %s", message)


def _record_success(path_text: str, source: Source, result: ProcessedFeature) -> bool:
    with transaction.atomic():
        row = SiteSettings.objects.select_for_update().filter(pk=1, feature_image_path=path_text).first()
        if row is None:
            return False  # the admin changed the path meanwhile; the next poll looks at the new one
        new_image = row.feature_image != result.full_name
        SiteSettings.objects.filter(pk=1).update(
            feature_image=result.full_name,
            feature_image_small=result.small_name,
            feature_image_width=result.width,
            feature_image_height=result.height,
            feature_image_updated=source.modified,
            feature_source_sig=source.signature,
            feature_error="",
        )
        if new_image or row.feature_error:
            bump_data_version()  # TD-28: cached pages must show the new image (or lose the old error)
    media_root = Path(settings.MEDIA_ROOT)
    prune_feature_files(media_root, (result.full_name, result.small_name))
    if new_image:
        log.info("front-page image updated (%dx%d)", result.width, result.height)
    return new_image


# --- the polling thread ---------------------------------------------------------------------------------------------

_poller_lock = threading.Lock()
_poller: threading.Thread | None = None


def start_poller(
    interval_s: float = POLL_INTERVAL_S, *, sync_fn: Callable[[], SyncResult] | None = None
) -> threading.Thread | None:
    """Start the daemon thread that polls the source file (once per process; a second call does nothing).

    Every failure is logged and ignored: the thread must outlive a locked database, a missing table before the first
    migration, or an unreachable share."""
    global _poller
    with _poller_lock:
        if _poller is not None and _poller.is_alive():
            return None
        stop = threading.Event()
        work = sync_fn or sync

        def loop() -> None:
            while not stop.wait(interval_s):
                try:
                    work()
                except DatabaseError as exc:
                    log.debug("front-page image poll skipped: %s", exc)
                except Exception:
                    log.exception("front-page image poll failed")
                finally:
                    connections.close_all()  # a thread's connection is never reused by anyone else

        _poller = threading.Thread(target=loop, name="il2ks-feature-image", daemon=True)
        _poller.start()
        return _poller


def poller_enabled() -> bool:
    """Tests and management commands import `il2ks.wsgi` too (or not); `IL2KS_NO_POLLER=1` switches the thread off."""
    return os.environ.get("IL2KS_NO_POLLER", "") == ""
