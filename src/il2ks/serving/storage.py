"""Static file storage for production (TD-28): hashed names, pre-compressed copies, served by WhiteNoise."""

from whitenoise.storage import CompressedManifestStaticFilesStorage  # pyright: ignore[reportMissingTypeStubs]


class LenientManifestStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's hashed + compressed storage, minus the crash on a file missing from the manifest.

    The default raises an error while rendering a page that mentions a static file `collectstatic` has not seen (a typo
    in a `custom/` template, an override added since the last start). Here the page still renders and that one file is
    simply a 404, which an admin can live with and `il2ks doctor` / the web log point out. It also keeps tests that
    render templates working without running `collectstatic` first."""

    manifest_strict = False
