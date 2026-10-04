"""Serves the admin-uploaded logo and fonts at `/media/branding/<file>` (FR-ADM-2, NFR-SEC-7).

WhiteNoise only serves collected static files, so uploads need a view. It is deliberately narrow: one folder
(`MEDIA_ROOT/branding/`), flat file names of a safe alphabet, raster image and WOFF/WOFF2 font extensions only.
Everything else is a 404, with no hint of why. Files are content-hashed (`web.logo`, `web.fonts`), so they are cached
for a year. Works with DEBUG off.
"""

import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponseNotModified
from django.http.response import HttpResponseBase
from django.utils.cache import get_conditional_response
from django.utils.http import http_date
from django.views.decorators.http import require_safe

from il2ks.web.logo import BRANDING_DIR

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@require_safe
def serve_media(request: HttpRequest, path: str) -> HttpResponseBase:
    """GET/HEAD `branding/<name>`; 404 for anything that isn't exactly that and a regular file inside it."""
    folder, _, name = path.partition("/")
    suffix = Path(name).suffix.lower()
    if folder != BRANDING_DIR or not SAFE_NAME.fullmatch(name) or ".." in name or suffix not in CONTENT_TYPES:
        raise Http404
    root = Path(settings.MEDIA_ROOT)
    branding = (root / BRANDING_DIR).resolve()
    target = (branding / name).resolve()  # resolves symlinks: a link pointing out of the folder is refused below
    if target.parent != branding or not target.is_file():
        raise Http404

    etag = f'"{name}"'  # the name contains the content hash
    last_modified = int(target.stat().st_mtime)
    not_modified = get_conditional_response(request, etag=etag, last_modified=last_modified)
    if isinstance(not_modified, HttpResponseNotModified):
        response: HttpResponseBase = not_modified
    else:
        response = FileResponse(target.open("rb"), content_type=CONTENT_TYPES[suffix])
        response["Content-Length"] = str(target.stat().st_size)
        response["Content-Disposition"] = "inline"
    response["ETag"] = etag
    response["Last-Modified"] = http_date(last_modified)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
