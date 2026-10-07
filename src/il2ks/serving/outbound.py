"""The one door for requests the server makes on a URL somebody typed (NFR-SEC-9): an SSRF-guarded GET.

Without a guard, "fetch this URL" lets an admin (or whoever steals the admin's password) point the server at its
own loopback services, the LAN or a cloud metadata address (169.254.169.254) and read the answer back. Every
outbound request therefore goes through `fetch`:

- only `http` and `https`; no user name or password inside the URL;
- the host name is resolved first, and **every** address it resolves to must be public (see `is_public`). Loopback,
  private, link-local (cloud metadata), shared (100.64/10), multicast, reserved and unspecified addresses are refused,
  and so are IPv4-mapped / NAT64 / 6to4 IPv6 forms of those;
- the connection goes to the address that was checked. The host name only appears in the TLS SNI (and certificate check)
  and the `Host` header, and nothing resolves a second time, so DNS rebinding cannot swap the target after the check;
- at most `MAX_REDIRECTS` redirects, each target checked the same way (scheme, then resolve, then addresses);
- a size cap on the body (`max_bytes`) and a timeout (`timeout_s`) for the whole call: connecting, the TLS handshake,
  the headers and the body. A watchdog timer shuts the socket at the deadline, so a server that sends one byte every
  few seconds cannot hold the call. The watchdog only shuts sockets down; the thread that owns the call closes them
  (so a reused descriptor number is never touched from the timer thread). Answers that close the connection
  (`Connection: close`, HTTP/1.0, a body that ends with the close) are read to the end like any other.
  (Resolving the host name through the system resolver is the one step that cannot be interrupted.)
- `[outbound] allow_private` (il2ks.toml): networks an admin explicitly allows (a source on the LAN). Off by default.

Only the standard library is used. The resolver, the TCP connection and the TLS wrapping are parameters, so tests run
without a network.
"""

import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_TIMEOUT_S = 10.0
USER_AGENT = "il2ks"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 16_384

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type Resolver = Callable[[str, int], Sequence[str]]
"""`(host, port) -> addresses` as text. Tests pass a fake."""
type Connector = Callable[[str, int, float], socket.socket]
"""`(address, port, timeout) -> connected socket`. Tests pass a fake."""
type TlsWrapper = Callable[[socket.socket, str], socket.socket]
"""`(connected socket, host name) -> TLS socket`: certificate checked against the name, name sent as SNI."""


class OutboundError(Exception):
    """The request was refused or failed. The message is in plain words and safe to show to the admin."""


class BlockedAddressError(OutboundError):
    """The URL resolves to an address that is not public (and not allowed by `[outbound] allow_private`)."""


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str  # the final URL, after redirects
    status: int
    headers: dict[str, str]  # names lower-cased
    body: bytes


# --- address rules -------------------------------------------------------------------------------

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


def _embedded_v4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address hidden inside an IPv6 one (IPv4-mapped `::ffff:a.b.c.d`, NAT64, 6to4), else None."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)
    return None


def is_public(address: IPAddress) -> bool:
    """Whether a server may connect to `address` without special permission."""
    if isinstance(address, ipaddress.IPv6Address):
        inner = _embedded_v4(address)
        if inner is not None:
            return is_public(inner)
    if (
        isinstance(address, ipaddress.IPv6Address) and address.is_site_local
    ):  # fec0::/10, deprecated but routable inside
        return False
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def parse_allow_list(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """`[outbound] allow_private` entries (address or network, `192.168.1.0/24`) as networks; ValueError on junk."""
    return tuple(ipaddress.ip_network(entry.strip(), strict=False) for entry in entries)


def _allowed(address: IPAddress, allow: Sequence[IPNetwork]) -> bool:
    candidates: list[IPAddress] = [address]
    if isinstance(address, ipaddress.IPv6Address) and (inner := _embedded_v4(address)) is not None:
        candidates.append(inner)  # `::ffff:192.168.1.5` is allowed by an entry for 192.168.1.0/24
    return any(c.version == net.version and c in net for c in candidates for net in allow)


# --- default plumbing ----------------------------------------------------------------------------


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundError(f"the host name {host!r} could not be resolved") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def system_connector(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout)


def system_tls(sock: socket.socket, host: str) -> socket.socket:
    return ssl.create_default_context().wrap_socket(sock, server_hostname=host)


# --- the guarded request -------------------------------------------------------------------------


def checked_addresses(host: str, port: int, *, resolver: Resolver, allow: Sequence[IPNetwork] = ()) -> list[str]:
    """Resolve `host` once; its addresses, or `BlockedAddressError` unless every one is public or allowed."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        names = list(resolver(host, port))
    else:
        names = [str(literal)]
    if not names:
        raise OutboundError(f"the host name {host!r} has no address")
    addresses: list[str] = []
    for name in names:
        try:
            address = ipaddress.ip_address(name.split("%", 1)[0])  # drop an IPv6 zone id
        except ValueError as exc:
            raise OutboundError(f"the resolver gave {name!r} for {host!r}, which is not an address") from exc
        if not is_public(address) and not _allowed(address, allow):
            raise BlockedAddressError(f"{host!r} points to {address}, which is not a public address")
        addresses.append(str(address))
    return addresses


def _split(url: str) -> tuple[str, str, int, str]:
    """`(scheme, host, port, request target)` of an http(s) URL without credentials."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise OutboundError(f"only http and https addresses are allowed, not {parts.scheme or 'none'!r}")
    if parts.username is not None or parts.password is not None:
        raise OutboundError("the address must not contain a user name or password")
    host = parts.hostname
    if not host:
        raise OutboundError("the address has no host name")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise OutboundError("the address has an invalid port") from exc
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return parts.scheme, host, port, target


def _shutdown(sock: socket.socket) -> None:
    """Shut a socket down (safe from another thread): that wakes a `recv` that is blocked on it. It does not close the
    descriptor: only the thread that owns the socket does that, or the number could be reused under its feet."""
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def _close(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        sock.close()


class _PinnedConnection(http.client.HTTPConnection):
    """An `HTTPConnection` that connects to an address we checked and sends the host name in `Host` (and in the TLS
    handshake when `tls` is given). `http.client` never resolves anything itself here.

    `start_watchdog(seconds)` makes the deadline hard: when it passes, every socket of the connection is shut down
    (not closed), wherever the call is blocked (connect, handshake, headers, body). `finish()` is the owner's clean-up:
    cancel the timer, close everything. `close()` stays `http.client`'s own: `getresponse()` calls it for an answer that
    will close (`Connection: close`, HTTP/1.0), and the body is still read from the socket afterwards."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        addresses: Sequence[str],
        timeout: float,
        connector: Connector,
        tls: TlsWrapper | None,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self._addresses = addresses
        self._connector = connector
        self._tls = tls
        self._watchdog: threading.Timer | None = None
        self._open: list[socket.socket] = []  # the raw socket during the handshake, then the TLS one
        self.expired = threading.Event()

    def start_watchdog(self, seconds: float) -> None:
        timer = threading.Timer(max(seconds, 0.0), self._expire)
        timer.daemon = True
        self._watchdog = timer
        timer.start()

    def _expire(self) -> None:
        self.expired.set()
        for sock in list(self._open):
            _shutdown(sock)

    def _track(self, sock: socket.socket) -> None:
        self._open.append(sock)
        if self.expired.is_set():  # the timer fired just before the socket was listed
            _shutdown(sock)  # closed by `finish()`
            raise OutboundError("the request took too long")

    def connect(self) -> None:
        last: OSError | None = None
        for address in self._addresses:
            try:
                sock = self._connector(address, self.port or 0, self.timeout or 0.0)
            except OSError as exc:
                last = exc
                continue
            try:
                self._track(sock)
                if self._tls is not None:
                    wrapped = self._tls(sock, self.host)
                    self._track(wrapped)
                    sock = wrapped
            except BaseException:
                for opened in self._open:
                    _close(opened)  # a failed handshake must not leave the raw socket open
                raise
            self.sock = sock
            return
        raise OutboundError(f"could not connect to {self.host!r}") from last

    @property
    def body_socket(self) -> socket.socket | None:
        """The socket the answer is read from (`self.sock` is None once `http.client` has closed the connection)."""
        return self._open[-1] if self._open else None

    def finish(self) -> None:
        """Cancel the watchdog and close every socket. Called by the thread that owns the connection."""
        if self._watchdog is not None:
            self._watchdog.cancel()
        super().close()
        for sock in self._open:
            _close(sock)


def fetch(
    url: str,
    *,
    allow_private: Sequence[IPNetwork] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    resolver: Resolver = system_resolver,
    connector: Connector = system_connector,
    tls: TlsWrapper = system_tls,
    clock: Callable[[], float] = time.monotonic,
) -> Fetched:
    """GET `url` through the guard. Raises `OutboundError` (a `BlockedAddressError` for a non-public target) when the
    request is refused, fails, takes longer than `timeout_s` in total, redirects more than `max_redirects` times, or the
    body is larger than `max_bytes`. A status like 404 is returned, not raised: the caller decides."""
    deadline = clock() + timeout_s
    current = url
    for _hop in range(max_redirects + 1):
        scheme, host, port, target = _split(current)
        addresses = checked_addresses(host, port, resolver=resolver, allow=allow_private)
        remaining = deadline - clock()
        if remaining <= 0:
            raise OutboundError("the request took too long")
        response, connection = _request(
            scheme, host, port, target, addresses, remaining, connector, tls if scheme == "https" else None
        )
        try:
            status = response.status
            headers = {name.lower(): value for name, value in response.getheaders()}
            if status in _REDIRECT_STATUSES and "location" in headers:
                current = urljoin(current, headers["location"])
                continue
            return Fetched(current, status, headers, _read_capped(response, connection, max_bytes, deadline, clock))
        finally:
            response.close()
            connection.finish()
    raise OutboundError(f"more than {max_redirects} redirects")


def _request(
    scheme: str,
    host: str,
    port: int,
    target: str,
    addresses: Sequence[str],
    timeout: float,
    connector: Connector,
    tls: TlsWrapper | None,
) -> tuple[http.client.HTTPResponse, _PinnedConnection]:
    default_port = 443 if scheme == "https" else 80
    host_header = host if port == default_port else f"{host}:{port}"
    if ":" in host:  # an IPv6 literal
        host_header = f"[{host}]" + ("" if port == default_port else f":{port}")
    connection = _PinnedConnection(host, port, addresses=addresses, timeout=timeout, connector=connector, tls=tls)
    connection.start_watchdog(timeout)
    try:
        connection.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host_header)
        connection.putheader("User-Agent", USER_AGENT)
        connection.putheader("Accept-Encoding", "identity")
        connection.putheader("Connection", "close")
        connection.endheaders()
        return connection.getresponse(), connection
    except (OutboundError, OSError, http.client.HTTPException, ValueError) as exc:
        late = connection.expired.is_set() or isinstance(exc, TimeoutError)
        connection.finish()
        if isinstance(exc, OutboundError):
            raise
        if late:
            raise OutboundError("the request took too long") from exc
        raise OutboundError(f"the request to {host!r} failed: {exc}") from exc


def _read_capped(
    response: http.client.HTTPResponse,
    connection: _PinnedConnection,
    max_bytes: int,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    declared = response.getheader("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise OutboundError(f"the response is larger than {max_bytes} bytes")
    body = bytearray()
    try:
        while True:
            if response.isclosed():  # the whole body was read: `http.client` closed the socket behind the last byte
                break
            left = deadline - clock()
            if left <= 0:
                raise OutboundError("the request took too long")
            if (body_socket := connection.body_socket) is not None:
                body_socket.settimeout(left)  # one `recv` never waits longer than what is left of the call
            chunk = response.read1(min(_CHUNK, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body += chunk
            if len(body) > max_bytes:
                raise OutboundError(f"the response is larger than {max_bytes} bytes")
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if connection.expired.is_set() or isinstance(exc, TimeoutError):
            raise OutboundError("the request took too long") from exc
        raise OutboundError(f"reading the response failed: {exc}") from exc
    if connection.expired.is_set():  # the watchdog's shutdown looks like the end of a close-delimited body
        raise OutboundError("the request took too long")
    return bytes(body)
