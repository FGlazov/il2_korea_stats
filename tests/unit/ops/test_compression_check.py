"""Doctor: are the pages compressed? (0.2.0 ops item). The decision logic with a fake fetcher, the real fetch against a
tiny local HTTP server, and the public address helper."""

from __future__ import annotations

import http.server
import socket
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import pytest

from il2ks.config import Config, HttpsConfig
from il2ks.ops import compression_check
from il2ks.ops.compression_check import FetchFailed, pages_compressed
from il2ks.ops.doctor import Level, run_checks
from il2ks.serving.djsettings import public_base_url
from tests.ops_helpers import make_instance


def external(tmp_path: Path, domain: str = "stats.example.com", **https: object) -> Config:
    cfg = make_instance(tmp_path)
    return replace(cfg, https=replace(HttpsConfig(), mode="external", domain=domain, **https))


def calls_to(monkeypatch: pytest.MonkeyPatch, answer: str | Exception) -> list[str]:
    seen: list[str] = []

    def fake(url: str) -> str:
        seen.append(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(compression_check, "fetcher", fake)
    return seen


def test_uncompressed_pages_are_a_warning_with_the_docs_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calls_to(monkeypatch, "")
    [finding] = pages_compressed(external(tmp_path))
    assert seen == ["https://stats.example.com/"]
    assert finding.level is Level.WARN
    assert "not compressed" in finding.title
    assert "reverse-proxy.md" in finding.fix


def test_compressed_pages_are_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls_to(monkeypatch, "br")
    [finding] = pages_compressed(external(tmp_path))
    assert finding.level is Level.OK
    assert "br" in finding.detail


def test_no_public_address_is_skipped_with_an_info_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = calls_to(monkeypatch, "")
    [finding] = pages_compressed(external(tmp_path, domain=""))
    assert finding.level is Level.OK
    assert "no [https] domain" in finding.detail
    assert seen == []


def test_the_bundled_caddy_is_skipped_with_an_info_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = calls_to(monkeypatch, "")
    cfg = replace(make_instance(tmp_path), https=HttpsConfig(mode="caddy", domain="stats.example.com"))
    [finding] = pages_compressed(cfg)
    assert finding.level is Level.OK
    assert "Caddy" in finding.detail
    assert seen == []


def test_a_network_problem_is_a_warning_never_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls_to(monkeypatch, FetchFailed("Name or service not known"))
    [finding] = pages_compressed(external(tmp_path))
    assert finding.level is Level.WARN
    assert "Name or service not known" in finding.detail


def test_the_check_is_registered_with_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls_to(monkeypatch, FetchFailed("timed out"))
    findings = run_checks(external(tmp_path))
    assert any(f.title.startswith("Could not check whether the pages are compressed") for f in findings)


def test_public_base_url(tmp_path: Path) -> None:
    base = make_instance(tmp_path)
    assert public_base_url(replace(base, https=HttpsConfig(mode="external"))) == ""
    ext = replace(base, https=HttpsConfig(mode="external", domain="stats.example.com"))
    assert public_base_url(ext) == "https://stats.example.com"
    assert public_base_url(replace(ext, https=replace(ext.https, https_port=8443))) == "https://stats.example.com"
    assert public_base_url(replace(base, https=HttpsConfig(domain="stats.example.com", https_port=8443))) == (
        "https://stats.example.com:8443"
    )
    assert public_base_url(replace(base, https=HttpsConfig(domain="2001:db8::1"))) == "https://[2001:db8::1]"


# --- the real fetch ---------------------------------------------------------------------------------------------------


class Handler(http.server.BaseHTTPRequestHandler):
    wanted: ClassVar[list[str]] = []
    encoding = ""
    status = 200

    def do_GET(self) -> None:
        type(self).wanted.append(self.headers.get("Accept-Encoding", ""))
        self.send_response(type(self).status)
        if type(self).encoding:
            self.send_header("Content-Encoding", type(self).encoding)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Handler.wanted, Handler.encoding, Handler.status = [], "", 200
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()


def test_the_fetch_asks_for_gzip_br_zstd_and_reports_the_content_encoding(server: str) -> None:
    assert compression_check.fetch_content_encoding(server) == ""
    Handler.encoding = "gzip"
    assert compression_check.fetch_content_encoding(server) == "gzip"
    assert Handler.wanted == ["gzip, br, zstd", "gzip, br, zstd"]


def test_the_fetch_turns_http_errors_into_a_plain_reason(server: str) -> None:
    Handler.status = 503
    with pytest.raises(FetchFailed, match="503"):
        compression_check.fetch_content_encoding(server)


def test_the_fetch_turns_a_refused_connection_into_a_plain_reason() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(FetchFailed):
        compression_check.fetch_content_encoding(f"http://127.0.0.1:{port}/")


def test_the_fetch_gives_up_after_the_short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compression_check, "TIMEOUT_S", 0.3)
    with socket.socket() as silent:  # accepts connections (backlog) but never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        with pytest.raises(FetchFailed, match=r"no answer|timed out"):
            compression_check.fetch_content_encoding(f"http://127.0.0.1:{silent.getsockname()[1]}/")
