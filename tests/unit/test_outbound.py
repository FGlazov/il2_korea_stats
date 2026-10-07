"""The SSRF-guarded fetch (NFR-SEC-9): fake resolver, fake sockets, no network."""

import io
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Sequence

import pytest

from il2ks.serving import outbound
from il2ks.serving.outbound import BlockedAddressError, OutboundError, fetch, is_public, parse_allow_list

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "9.9.9.9"


class FakeSocket:
    """Plays a canned HTTP response and records what the client sent."""

    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = b""
        self.closed = False
        self.timeouts: list[float | None] = []

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def shutdown(self, how: int) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def makefile(self, mode: str = "rb", buffering: int | None = None) -> io.BytesIO:
        return io.BytesIO(self.response)

    def close(self) -> None:
        self.closed = True


def http(status: str = "200 OK", body: bytes = b"hello", **headers: str) -> bytes:
    lines = [f"HTTP/1.1 {status}", *(f"{k.replace('_', '-')}: {v}" for k, v in headers.items()), "", ""]
    return "\r\n".join(lines).encode() + body


class World:
    """The fake internet: a resolver table, and one canned response per (address) connection."""

    def __init__(self, dns: dict[str, list[str]], responses: Sequence[bytes]) -> None:
        self.dns = dns
        self.responses = list(responses)
        self.lookups: list[str] = []
        self.connections: list[tuple[str, int]] = []
        self.sockets: list[FakeSocket] = []
        self.tls_hosts: list[str] = []
        self.refuse: set[str] = set()

    def resolver(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        return self.dns[host]

    def connector(self, address: str, port: int, timeout: float) -> socket.socket:
        self.connections.append((address, port))
        if address in self.refuse:
            raise ConnectionRefusedError(address)
        sock = FakeSocket(self.responses.pop(0))
        self.sockets.append(sock)
        return sock  # type: ignore[return-value]

    def tls(self, sock: socket.socket, host: str) -> socket.socket:
        self.tls_hosts.append(host)
        return sock

    def get(self, url: str, **kwargs: object) -> outbound.Fetched:
        return fetch(url, resolver=self.resolver, connector=self.connector, tls=self.tls, **kwargs)  # type: ignore[arg-type]


# --- addresses -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",
        "239.255.255.250",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fc00::1",
        "fd12:3456::1",
        "fe80::1",
        "ff02::1",
        "fec0::1",  # site-local (deprecated, but still not the public internet)
        "feff::1",
        "::ffff:127.0.0.1",  # IPv4-mapped forms of the above
        "::ffff:10.0.0.5",
        "::ffff:169.254.169.254",
        "::ffff:192.168.1.1",
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "2002:7f00:1::1",  # 6to4 of 127.0.0.1
        "2002:a9fe:a9fe::1",  # 6to4 of 169.254.169.254
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address", [PUBLIC, "8.8.8.8", "1.1.1.1", "172.32.0.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public(ipaddress.ip_address(address))


def test_the_allow_list_takes_addresses_and_networks() -> None:
    nets = parse_allow_list(["192.168.1.0/24", "10.0.0.5"])
    assert [str(n) for n in nets] == ["192.168.1.0/24", "10.0.0.5/32"]
    with pytest.raises(ValueError, match="not-a-network"):
        parse_allow_list(["not-a-network"])


# --- the request ---------------------------------------------------------------------------------


def test_a_plain_fetch_connects_to_the_resolved_address_and_sends_the_host_name() -> None:
    world = World({"example.org": [PUBLIC]}, [http(body=b"page")])

    result = world.get("http://example.org/doc.md?x=1")

    assert (result.status, result.body, result.url) == (200, b"page", "http://example.org/doc.md?x=1")
    assert world.connections == [(PUBLIC, 80)]
    request = world.sockets[0].sent.decode()
    assert request.startswith("GET /doc.md?x=1 HTTP/1.1\r\n")
    assert "Host: example.org\r\n" in request
    assert world.tls_hosts == []


def test_https_uses_port_443_and_the_host_name_for_tls() -> None:
    world = World({"example.org": [PUBLIC]}, [http()])

    world.get("https://example.org:8443/")
    assert world.connections == [(PUBLIC, 8443)]
    assert world.tls_hosts == ["example.org"]
    assert "Host: example.org:8443\r\n" in world.sockets[0].sent.decode()

    world = World({"example.org": [PUBLIC]}, [http()])
    world.get("https://example.org/")
    assert world.connections == [(PUBLIC, 443)]
    assert "Host: example.org\r\n" in world.sockets[0].sent.decode()


def test_the_name_is_resolved_once_per_request_and_never_again() -> None:
    """The checked address is the connected address: a second lookup (DNS rebinding) never happens."""
    world = World({"example.org": [PUBLIC]}, [http()])

    world.get("http://example.org/")

    assert world.lookups == ["example.org"]


def test_a_rebinding_resolver_cannot_swap_the_target() -> None:
    answers = iter([[PUBLIC], ["127.0.0.1"]])
    world = World({}, [http()])

    def resolver(host: str, port: int) -> list[str]:
        return next(answers)

    result = fetch("http://rebind.example/", resolver=resolver, connector=world.connector, tls=world.tls)

    assert result.status == 200
    assert world.connections == [(PUBLIC, 80)]


@pytest.mark.parametrize("scheme", ["ftp", "file", "gopher", "javascript"])
def test_only_http_and_https(scheme: str) -> None:
    world = World({}, [])
    with pytest.raises(OutboundError, match="only http and https"):
        world.get(f"{scheme}://example.org/x")
    assert world.connections == []


def test_credentials_in_the_url_are_refused() -> None:
    world = World({"example.org": [PUBLIC]}, [])
    with pytest.raises(OutboundError, match="user name"):
        world.get("http://user:pw@example.org/")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://10.1.2.3:8080/",
    ],
)
def test_private_targets_are_refused_before_connecting(url: str) -> None:
    world = World({"localhost": ["127.0.0.1", "::1"]}, [])

    with pytest.raises(BlockedAddressError):
        world.get(url)

    assert world.connections == []


def test_one_private_answer_among_public_ones_blocks_the_host() -> None:
    world = World({"mixed.example": [PUBLIC, "10.0.0.9"]}, [http()])

    with pytest.raises(BlockedAddressError, match=r"10\.0\.0\.9"):
        world.get("http://mixed.example/")

    assert world.connections == []


def test_a_resolver_answer_that_is_no_address_is_refused() -> None:
    world = World({"odd.example": ["not-an-address"]}, [])
    with pytest.raises(OutboundError, match="not an address"):
        world.get("http://odd.example/")


def test_an_unresolvable_name_is_an_error() -> None:
    def resolver(host: str, port: int) -> list[str]:
        raise OutboundError("could not be resolved")

    with pytest.raises(OutboundError):
        fetch("http://nowhere.example/", resolver=resolver)


def test_allow_private_lets_a_lan_source_through_and_only_that_network() -> None:
    allow = parse_allow_list(["192.168.1.0/24"])
    world = World({"nas.lan": ["192.168.1.20"], "other.lan": ["192.168.2.20"], "meta": ["169.254.169.254"]}, [http()])

    assert world.get("http://nas.lan/file.md", allow_private=allow).status == 200
    assert world.connections == [("192.168.1.20", 80)]
    for host in ("other.lan", "meta"):
        with pytest.raises(BlockedAddressError):
            world.get(f"http://{host}/", allow_private=allow)


def test_allow_private_covers_the_ipv4_mapped_form() -> None:
    allow = parse_allow_list(["192.168.1.0/24"])
    world = World({"nas.lan": ["::ffff:192.168.1.20"]}, [http()])

    assert world.get("http://nas.lan/", allow_private=allow).status == 200


def test_a_refused_connection_tries_the_next_checked_address() -> None:
    world = World({"example.org": [PUBLIC, OTHER_PUBLIC]}, [http(body=b"second")])
    world.refuse = {PUBLIC}

    assert world.get("http://example.org/").body == b"second"
    assert [a for a, _ in world.connections] == [PUBLIC, OTHER_PUBLIC]


def test_all_connections_failing_is_an_outbound_error() -> None:
    world = World({"example.org": [PUBLIC]}, [])
    world.refuse = {PUBLIC}

    with pytest.raises(OutboundError, match="could not connect"):
        world.get("http://example.org/")


# --- redirects -----------------------------------------------------------------------------------


def test_a_redirect_is_followed_and_its_target_is_resolved_and_checked() -> None:
    world = World(
        {"a.example": [PUBLIC], "b.example": [OTHER_PUBLIC]},
        [http("302 Found", b"", Location="https://b.example/moved"), http(body=b"there")],
    )

    result = world.get("http://a.example/start")

    assert (result.body, result.url) == (b"there", "https://b.example/moved")
    assert world.connections == [(PUBLIC, 80), (OTHER_PUBLIC, 443)]
    assert world.lookups == ["a.example", "b.example"]


def test_a_relative_redirect_stays_on_the_host() -> None:
    world = World({"a.example": [PUBLIC]}, [http("301 Moved", b"", Location="/new/path"), http()])

    assert world.get("http://a.example/old").url == "http://a.example/new/path"


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://internal.example/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_a_redirect_to_a_private_address_is_refused(target: str) -> None:
    world = World({"a.example": [PUBLIC], "internal.example": ["10.0.0.7"]}, [http("302 Found", b"", Location=target)])

    with pytest.raises(BlockedAddressError):
        world.get("http://a.example/")

    assert world.connections == [(PUBLIC, 80)]  # never connected to the target


def test_a_redirect_to_another_scheme_is_refused() -> None:
    world = World({"a.example": [PUBLIC]}, [http("302 Found", b"", Location="file:///etc/passwd")])

    with pytest.raises(OutboundError, match="only http and https"):
        world.get("http://a.example/")


def test_three_redirects_are_followed_the_fourth_is_not() -> None:
    hop = http("302 Found", b"", Location="/next")
    assert World({"a.example": [PUBLIC]}, [hop, hop, hop, http()]).get("http://a.example/").status == 200

    with pytest.raises(OutboundError, match="more than 3 redirects"):
        World({"a.example": [PUBLIC]}, [hop, hop, hop, hop, http()]).get("http://a.example/")


# --- limits --------------------------------------------------------------------------------------


def test_a_body_over_the_cap_is_refused_even_without_a_content_length() -> None:
    world = World({"a.example": [PUBLIC]}, [http(body=b"x" * 101)])

    with pytest.raises(OutboundError, match="larger than 100 bytes"):
        world.get("http://a.example/", max_bytes=100)


def test_a_declared_length_over_the_cap_is_refused_at_once() -> None:
    world = World({"a.example": [PUBLIC]}, [http(body=b"x" * 10, Content_Length="999999999")])

    with pytest.raises(OutboundError, match="larger than 100 bytes"):
        world.get("http://a.example/", max_bytes=100)


def test_a_body_exactly_at_the_cap_is_fine() -> None:
    world = World({"a.example": [PUBLIC]}, [http(body=b"x" * 100)])

    assert len(world.get("http://a.example/", max_bytes=100).body) == 100


def test_the_total_time_is_capped() -> None:
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
    world = World({"a.example": [PUBLIC]}, [http(body=b"x" * 50_000)])

    with pytest.raises(OutboundError, match="took too long"):
        fetch(
            "http://a.example/",
            timeout_s=5,
            resolver=world.resolver,
            connector=world.connector,
            tls=world.tls,
            clock=lambda: next(ticks),
            max_bytes=1_000_000,
        )


def test_the_socket_timeout_is_what_is_left_of_the_budget() -> None:
    seen: list[float] = []

    def connector(address: str, port: int, timeout: float) -> socket.socket:
        seen.append(timeout)
        return FakeSocket(http())  # type: ignore[return-value]

    fetch("http://a.example/", timeout_s=7, resolver=lambda h, p: [PUBLIC], connector=connector)

    assert 0 < seen[0] <= 7


def test_an_error_status_is_returned_not_raised() -> None:
    world = World({"a.example": [PUBLIC]}, [http("404 Not Found", b"nope")])

    result = world.get("http://a.example/")

    assert (result.status, result.body) == (404, b"nope")


def test_the_system_resolver_reports_failure_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", failing)

    with pytest.raises(OutboundError, match="could not be resolved"):
        outbound.system_resolver("nowhere.invalid", 80)


def test_a_failed_tls_handshake_closes_the_raw_socket() -> None:
    """M4: the raw socket used to be left open until the garbage collector found it."""
    raw = FakeSocket(b"")

    def tls(sock: socket.socket, host: str) -> socket.socket:
        raise ssl.SSLError("handshake failed")

    with pytest.raises(OutboundError, match="failed"):
        fetch(
            "https://a.example/",
            resolver=lambda h, p: [PUBLIC],
            connector=lambda address, port, timeout: raw,  # type: ignore[arg-type,return-value]
            tls=tls,
        )

    assert raw.closed


# --- the whole-call deadline against a real, slow server (no internet: a socket on this machine) -----------------

LOOPBACK = parse_allow_list(["127.0.0.0/8"])


type Script = Callable[[socket.socket, threading.Event], None]


class SlowServer:
    """Accepts one connection and answers `script(connection, stop)`; stops after `give_up_s` at the latest."""

    def __init__(self, script: Script) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.port: int = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.script = script
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with connection:
            connection.settimeout(1)
            try:
                connection.recv(4096)
                self.script(connection, self.stop)
            except OSError:
                pass

    def __enter__(self) -> "SlowServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.listener.close()
        self.thread.join(5)


def trickle(data: bytes, every_s: float, give_up_s: float = 6.0) -> Script:
    def run(connection: socket.socket, stop: threading.Event) -> None:
        end = time.monotonic() + give_up_s
        for i in range(len(data)):
            if stop.is_set() or time.monotonic() > end:
                return
            connection.sendall(data[i : i + 1])
            time.sleep(every_s)

    return run


def local_fetch(server: SlowServer, timeout_s: float) -> float:
    """Seconds `fetch` needs to fail with 'took too long' (the test fails when it ends otherwise or late)."""
    start = time.monotonic()
    with pytest.raises(OutboundError, match="took too long"):
        fetch(
            f"http://localhost.example:{server.port}/",
            timeout_s=timeout_s,
            allow_private=LOOPBACK,
            resolver=lambda h, p: ["127.0.0.1"],
        )
    return time.monotonic() - start


def test_a_server_trickling_the_headers_cannot_hold_the_call_past_the_deadline() -> None:
    """M4: header reading had no deadline; one byte every 0.4 s kept the call alive for as long as the server liked."""
    head = http(body=b"", X_Slow="a" * 200)
    with SlowServer(trickle(head, 0.4)) as server:
        assert local_fetch(server, 1.0) < 3.0


def test_a_server_trickling_the_body_cannot_hold_the_call_past_the_deadline() -> None:
    head = http(body=b"", Content_Length="1000")
    body = b"x" * 1000

    def script(connection: socket.socket, stop: threading.Event) -> None:
        connection.sendall(head)
        trickle(body, 0.4)(connection, stop)

    with SlowServer(script) as server:
        assert local_fetch(server, 1.0) < 3.0


def test_a_server_that_never_answers_is_cut_off_at_the_deadline() -> None:
    def silent(connection: socket.socket, stop: threading.Event) -> None:
        stop.wait(6)

    with SlowServer(silent) as server:
        assert local_fetch(server, 1.0) < 3.0


def test_a_fast_local_server_still_works_through_the_real_sockets() -> None:
    answer = http(body=b"hello", Content_Length="5")

    def script(connection: socket.socket, stop: threading.Event) -> None:
        connection.sendall(answer)

    with SlowServer(script) as server:
        result = fetch(
            f"http://localhost.example:{server.port}/",
            allow_private=LOOPBACK,
            resolver=lambda h, p: ["127.0.0.1"],
        )

    assert (result.status, result.body) == (200, b"hello")


# --- `Connection: close` answers (H1): http.client closes the connection itself when the response will close ---


def serve_once(answer: bytes) -> Script:
    def script(connection: socket.socket, stop: threading.Event) -> None:
        connection.sendall(answer)

    return script


def local_get(server: SlowServer, max_bytes: int) -> outbound.Fetched:
    return fetch(
        f"http://localhost.example:{server.port}/",
        allow_private=LOOPBACK,
        resolver=lambda h, p: ["127.0.0.1"],
        max_bytes=max_bytes,
    )


def test_a_connection_close_answer_with_a_content_length_arrives_whole() -> None:
    """H1: getresponse() closed the connection, the override aborted the socket the body is read from."""
    body = bytes(range(256)) * 400  # 100 KB
    answer = http(body=body, Content_Length=str(len(body)), Connection="close")
    with SlowServer(serve_once(answer)) as server:
        result = local_get(server, 200_000)

    assert (result.status, result.body) == (200, body)


def test_a_chunked_connection_close_answer_arrives_whole() -> None:
    body = b"y" * 100_000
    chunked = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    answer = http(body=chunked, Transfer_Encoding="chunked", Connection="close")
    with SlowServer(serve_once(answer)) as server:
        result = local_get(server, 200_000)

    assert result.body == body


def test_a_close_delimited_http_1_0_body_is_not_truncated() -> None:
    body = b"z" * 100_000
    answer = b"HTTP/1.0 200 OK\r\n\r\n" + body  # no length: the end of the body is the close
    with SlowServer(serve_once(answer)) as server:
        result = local_get(server, 200_000)

    assert result.body == body


def test_a_trickled_connection_close_body_still_hits_the_deadline() -> None:
    """H1: with the connection closed early `connection.sock` was None and the body had no timeout at all."""
    head = http(body=b"", Connection="close")  # close-delimited: the server never says how long

    def script(connection: socket.socket, stop: threading.Event) -> None:
        connection.sendall(head)
        trickle(b"x" * 1000, 0.4)(connection, stop)

    with SlowServer(script) as server:
        assert local_fetch(server, 1.0) < 3.0


# --- clean-up after failures (L3) ----------------------------------------------------------------


def _timers() -> int:
    return sum(isinstance(t, threading.Timer) and t.is_alive() for t in threading.enumerate())


def test_a_failed_connect_cancels_the_watchdog_timer() -> None:
    """L3: the OutboundError from connect() skipped `connection.close()`, so the timer ran on to its deadline."""
    world = World({"example.org": [PUBLIC]}, [])
    world.refuse = {PUBLIC}
    before = _timers()

    with pytest.raises(OutboundError, match="could not connect"):
        world.get("http://example.org/", timeout_s=30.0)

    assert _timers() == before


def test_the_watchdog_only_shuts_the_socket_down_the_owner_closes_it() -> None:
    """L3: closing a descriptor from the timer thread while the owner sits in recv() can hit a reused fd."""
    sock = FakeSocket(b"")
    shutdowns: list[int] = []
    sock.shutdown = shutdowns.append  # type: ignore[method-assign]
    connection = outbound._PinnedConnection(  # pyright: ignore[reportPrivateUsage]
        "example.org",
        80,
        addresses=[PUBLIC],
        timeout=5.0,
        connector=lambda a, p, t: sock,  # type: ignore[arg-type,return-value]
        tls=None,
    )
    connection.connect()

    connection._expire()  # pyright: ignore[reportPrivateUsage]

    assert shutdowns and not sock.closed
    assert connection.expired.is_set()
    connection.finish()
    assert sock.closed
