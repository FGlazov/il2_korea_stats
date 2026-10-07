"""Doctor: are the pages compressed on their way to the visitor?

With your own reverse proxy (nginx, IIS, Apache) the pages are compressed only when the proxy does it
(`docs/reverse-proxy.md`, requirement 6); il2ks itself sends them uncompressed on purpose (Django's GZipMiddleware would
compress in the single Python process on every hit). The bundled Caddy always compresses (`encode zstd gzip`), so there
is nothing to check then.

The check fetches the home page through the configured public address, asking for `gzip, br, zstd`, and looks for a
`Content-Encoding` header. It is a plain HTTP request to the admin's own address, with a short timeout: a network
problem
(the name does not resolve from this machine, a firewall, a certificate the machine does not trust) is a warning with a
hint, never an error, and never stops the other checks.
"""

import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable

from il2ks import __version__
from il2ks.config import Config
from il2ks.ops.doctor import Finding, Level, check
from il2ks.serving.djsettings import public_base_url

TIMEOUT_S = 5.0
ACCEPT_ENCODING = "gzip, br, zstd"
DOCS = "docs/reverse-proxy.md (requirement 6: compress the pages; the samples include it)"


class FetchFailed(Exception):
    """The page could not be fetched; the message says why in plain words."""


def fetch_content_encoding(url: str) -> str:
    """The `Content-Encoding` header of `url` ("" when there is none). Raises `FetchFailed` with the reason.

    The body is never read: the response headers are all the check needs. Replaced in tests."""
    request = urllib.request.Request(
        url, headers={"Accept-Encoding": ACCEPT_ENCODING, "User-Agent": f"il2ks-doctor/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return str(response.headers.get("Content-Encoding", "") or "")
    except urllib.error.HTTPError as exc:
        raise FetchFailed(f"the server answered {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError | ssl.CertificateError):
            raise FetchFailed(f"the certificate was not accepted ({reason})") from exc
        raise FetchFailed(str(reason)) from exc
    except TimeoutError as exc:
        raise FetchFailed(f"no answer within {TIMEOUT_S:g} seconds") from exc
    except (OSError, ValueError) as exc:  # a reset connection, a malformed address, ...
        raise FetchFailed(str(exc)) from exc


fetcher: Callable[[str], str] = fetch_content_encoding
"""Replaced in tests; the real one makes a network request."""


@check
def pages_compressed(cfg: Config) -> Iterable[Finding]:
    title = "Page compression"
    base = public_base_url(cfg)
    if not base:
        yield Finding(Level.OK, title, "not checked: no [https] domain is set, so there is no public address to fetch")
        return
    if cfg.https.mode == "caddy":
        yield Finding(Level.OK, title, "not checked: the bundled Caddy always compresses the pages (zstd, gzip)")
        return
    url = base + "/"
    try:
        encoding = fetcher(url)
    except FetchFailed as exc:
        yield Finding(
            Level.WARN,
            "Could not check whether the pages are compressed",
            f"Fetching {url} failed: {exc}.",
            "Open the address in a browser. If it works there, this machine may not reach its own public address "
            "(a router without hairpin NAT, a firewall): the check is only a hint, compression can be fine.",
        )
        return
    if encoding:
        yield Finding(Level.OK, title, f"{url} came back with Content-Encoding: {encoding}")
        return
    yield Finding(
        Level.WARN,
        "The pages are not compressed",
        f"{url} was asked for {ACCEPT_ENCODING} and came back without Content-Encoding. Pages with long tables are "
        "five to ten times larger than they need to be, which phones on mobile data notice.",
        f"Turn on compression in your proxy: see {DOCS}.",
    )
