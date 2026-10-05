"""Static file storage for production (TD-28): minified, hashed names, pre-compressed copies, served by WhiteNoise."""

from collections.abc import Callable, Generator
from pathlib import PurePosixPath
from typing import Any, cast

import rcssmin  # pyright: ignore[reportMissingTypeStubs]
import rjsmin  # pyright: ignore[reportMissingTypeStubs]
from whitenoise.storage import (  # pyright: ignore[reportMissingTypeStubs]
    CompressedManifestStaticFilesStorage,
    MissingFileError,
)

# Never minified: Django's own admin files and every vendored library (`vendor/` folders; ours are already `.min`).
_SKIP_FIRST_PARTS = frozenset({"admin"})
_SKIP_PARTS = frozenset({"vendor"})


def _minify_js(text: str) -> str:
    return cast(str, rjsmin.jsmin(text, keep_bang_comments=True))  # pyright: ignore[reportUnknownMemberType]


def _minify_css(text: str) -> str:
    return cast(str, rcssmin.cssmin(text, keep_bang_comments=True))  # pyright: ignore[reportUnknownMemberType]


def minifier_for(name: str) -> Callable[[str], str] | None:
    """The minify function for a static file name, or None when the file is left as it is.

    Comments starting `/*!` (license headers, the `il2ks-template:` marker of an override) survive."""
    path = PurePosixPath(name.replace("\\", "/"))  # Windows collects with backslashes
    if ".min" in path.suffixes or path.parts[0] in _SKIP_FIRST_PARTS or _SKIP_PARTS & set(path.parts):
        return None
    if path.suffix == ".js":
        return _minify_js
    if path.suffix == ".css":
        return _minify_css
    return None


class LenientManifestStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's hashed + compressed storage that minifies our CSS and JS, minus the crash on a file missing from
    the manifest.

    Minifying: `collectstatic` has copied the files to `STATIC_ROOT` before `post_process` runs; the copies there are
    minified in place and handed on as the files to hash, so the hash in the file name (and the manifest) is of the
    minified content, while the repo sources and the files under `custom/static` stay readable. A minified copy is
    minified again on the next start, which changes nothing. Development uses plain storage, not this class.

    Lenient: the default raises an error while rendering a page that mentions a static file `collectstatic` has not
    seen (a typo in a `custom/` template, an override added since the last start). Here the page still renders and that
    one file is simply a 404, which an admin can live with and `il2ks doctor` / the web log point out. It also keeps
    tests that render templates working without running `collectstatic` first."""

    manifest_strict = False

    def post_process(
        self, paths: dict[str, Any], dry_run: bool = False, **options: object
    ) -> Generator[tuple[str, str, MissingFileError | bool], Any]:
        if not dry_run:
            for name in paths:
                minify = minifier_for(name)
                if minify is not None and self.exists(name):
                    target = self.path(name)
                    with open(target, encoding="utf-8", newline="") as f:
                        text = f.read()
                    with open(target, "w", encoding="utf-8", newline="") as f:
                        f.write(minify(text))
                    paths[name] = (self, name)  # hash and post-process the minified copy, not the source file
        yield from super().post_process(paths, dry_run=dry_run, **options)  # pyright: ignore[reportUnknownMemberType]
