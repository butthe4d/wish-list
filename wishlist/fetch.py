"""SSRF-safe HTTP(S) fetching of shop pages and images.

Members type shop URLs and the server fetches them, so everything here guards
against the server being used to reach hosts on the internal network (router,
NAS, cloud metadata endpoints, ...):

* only ``http``/``https`` on ports 80/443, no userinfo in the URL;
* the host is resolved exactly once and *every* resolved address must be
  public; the connection then goes to that checked IP (DNS pinning), while
  HTTPS still sends SNI and verifies the certificate against the hostname;
* every redirect hop is validated the same way, at most ``MAX_REDIRECTS``;
* the body is read incrementally and capped at ``max_bytes``;
* ``TIMEOUT`` is a total deadline per request (connect, headers and body) and
  ``TOTAL_TIMEOUT`` bounds the whole call including redirects;
* ``deny_networks`` lets the operator block "public" addresses that are in fact
  internal (own public IPv4 with hairpin NAT, native IPv6 prefix), from
  ``FETCH_DENY_CIDRS``;
* no proxies from the environment (``http.client`` never consults them).
"""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

PAGE_MAX_BYTES = 2_000_000
IMAGE_MAX_BYTES = 10_000_000
TIMEOUT = 8
MAX_REDIRECTS = 3
TOTAL_TIMEOUT = 20

ALLOWED_PORTS = frozenset({80, 443})
USER_AGENT = "Mozilla/5.0 (compatible; WishlistBot/1.0)"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HOSTNAME_RE = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)*\.?$")
# Characters left as-is when re-quoting the path and query: reserved and
# unreserved ASCII plus "%" so existing escapes survive. Everything else
# (spaces, controls, non-ASCII) is percent-encoded as UTF-8.
_PATH_SAFE = "!#$%&'()*+,/:;=?@[]~"

_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_SITE_LOCAL = ipaddress.ip_network("fec0::/10")


class FetchError(Exception):
    """The URL could not be fetched safely. Messages are for logs, not users."""


@dataclass
class FetchResult:
    content: bytes
    content_type: str
    final_url: str


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _embedded_ipv4(ip: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv4-mapped or NAT64 (64:ff9b::/96) address routes to."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_denied(ip: IPAddress, deny_networks: Sequence[IPNetwork]) -> bool:
    candidates = [ip]
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        candidates.append(embedded)
    # Only same-version pairs can match (an embedded IPv4 against IPv4 networks).
    return any(
        addr.version == net.version and addr in net for addr in candidates for net in deny_networks
    )


def is_public_address(ip: IPAddress) -> bool:
    """True only for globally routable unicast addresses.

    IPv6 addresses that embed an IPv4 address are judged by the embedded
    address (IPv4-mapped, NAT64 well-known prefix) or refused outright
    (IPv4-compatible, 6to4, Teredo), since they can route to IPv4 hosts.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return is_public_address(embedded)
        if ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip in _IPV4_COMPATIBLE or ip in _SITE_LOCAL or ip.scope_id:
            return False
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


@dataclass
class _Target:
    url: str
    scheme: str
    host: str  # IDNA/ASCII hostname or IP literal without brackets
    port: int
    request_target: str  # path + query, percent-encoded
    host_header: str


def _parse_url(url: str) -> _Target:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise FetchError(f"invalid URL: {e}") from e
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise FetchError(f"scheme not allowed: {scheme!r}")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise FetchError("userinfo in URL not allowed")
    raw_host = parts.hostname
    if not raw_host:
        raise FetchError("URL has no host")
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    if port not in ALLOWED_PORTS:
        raise FetchError(f"port not allowed: {port}")

    try:
        ip = ipaddress.ip_address(raw_host)
    except ValueError:
        ip = None
    if ip is not None:
        host = str(ip)
        host_for_header = f"[{host}]" if isinstance(ip, ipaddress.IPv6Address) else host
    else:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError as e:
            raise FetchError("invalid hostname") from e
        if not _HOSTNAME_RE.match(host):
            raise FetchError("invalid hostname")
        host_for_header = host

    host_header = host_for_header if port == _DEFAULT_PORTS[scheme] else f"{host_for_header}:{port}"
    request_target = quote(parts.path or "/", safe=_PATH_SAFE)
    if parts.query:
        request_target += "?" + quote(parts.query, safe=_PATH_SAFE)
    return _Target(url, scheme, host, port, request_target, host_header)


def _resolve(target: _Target, deny_networks: Sequence[IPNetwork]) -> IPAddress:
    """Resolve once and return an address to connect to; all must be public and not denied."""
    try:
        addresses = [ipaddress.ip_address(target.host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as e:
            raise FetchError(f"cannot resolve {target.host!r}") from e
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        except ValueError as e:
            raise FetchError("unexpected resolver result") from e
    if not addresses:
        raise FetchError(f"no addresses for {target.host!r}")
    for address in addresses:
        if not is_public_address(address):
            raise FetchError(f"non-public address for {target.host!r}")
        if _is_denied(address, deny_networks):
            raise FetchError(f"denied address for {target.host!r}")
    return addresses[0]


def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


class _Deadline:
    """Total time budget for one request.

    Socket timeouts only bound a single send/recv, so a server dripping one
    byte at a time could hold a worker forever. Once the socket is connected a
    watchdog timer shuts it down when the budget runs out, which wakes any
    blocked read; ``expired`` then tells the caller why the read ended.
    """

    def __init__(self, seconds: float):
        self._expires = time.monotonic() + seconds
        self._timer = None
        self._killer = None
        self._lock = threading.Lock()
        self.expired = False

    def remaining(self) -> float:
        left = self._expires - time.monotonic()
        if left <= 0:
            self.expired = True
            raise FetchError("timed out")
        return left

    def arm(self, sock: socket.socket) -> None:
        # A duplicate descriptor of the same socket: shutting it down ends the
        # connection even after ssl.wrap_socket has detached the original.
        self._killer = sock.dup()
        self._timer = threading.Timer(self.remaining(), self._fire)
        self._timer.daemon = True
        try:
            self._timer.start()
        except RuntimeError as e:  # e.g. thread limit reached
            self._timer = None
            self.cancel()
            raise FetchError("cannot start timeout watchdog") from e

    def _fire(self) -> None:
        with self._lock:
            self.expired = True
            if self._killer is not None:
                try:
                    self._killer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        with self._lock:
            if self._killer is not None:
                self._killer.close()
                self._killer = None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to an already validated IP, never resolving."""

    def __init__(self, host, port, *, ip, deadline):
        super().__init__(host, port, timeout=TIMEOUT)
        self._ip = ip
        self._deadline = deadline

    def connect(self):
        family = socket.AF_INET6 if isinstance(self._ip, ipaddress.IPv6Address) else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self._deadline.remaining())
            sock.connect((str(self._ip), self.port))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._deadline.arm(sock)
            sock.settimeout(self._deadline.remaining())
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    default_port = 443

    def connect(self):
        super().connect()
        # SNI and certificate verification use the hostname from the URL, the
        # TCP connection goes to the pinned IP.
        self.sock = _tls_context().wrap_socket(self.sock, server_hostname=self.host)


def _read_body(response: http.client.HTTPResponse, max_bytes: int, deadline: _Deadline) -> bytes:
    length = response.getheader("Content-Length")
    if length is not None and length.strip().isdigit() and int(length) > max_bytes:
        raise FetchError("response too large")
    chunks = []
    total = 0
    while total <= max_bytes:
        deadline.remaining()
        chunk = response.read1(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > max_bytes:
        raise FetchError("response too large")
    return b"".join(chunks)


def _fetch_once(target: _Target, ip: IPAddress, *, budget: float, max_bytes: int, accept: str):
    """One request within ``budget`` seconds; returns (status, location, content_type, body)."""
    connection_class = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
    deadline = _Deadline(budget)
    connection = connection_class(target.host, target.port, ip=ip, deadline=deadline)
    try:
        connection.request(
            "GET",
            target.request_target,
            headers={
                "Host": target.host_header,
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        status = response.status
        if status in _REDIRECT_STATUSES:
            return status, response.getheader("Location"), "", b""
        if not 200 <= status < 300:
            raise FetchError(f"HTTP status {status}")
        body = _read_body(response, max_bytes, deadline)
        if deadline.expired:
            raise FetchError("timed out")
        return status, None, response.getheader("Content-Type", ""), body
    except FetchError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as e:
        if deadline.expired:
            raise FetchError("timed out") from e
        raise FetchError(f"request failed: {e.__class__.__name__}") from e
    finally:
        deadline.cancel()
        connection.close()


def safe_fetch(
    url: str,
    *,
    max_bytes: int,
    accept: str,
    deny_networks: Sequence[IPNetwork] = (),
) -> FetchResult:
    """Fetch ``url`` with SSRF protections; raise FetchError on any refusal or failure.

    ``deny_networks`` (operator's ``FETCH_DENY_CIDRS``) are refused on every hop
    in addition to all non-public addresses.
    """
    expires = time.monotonic() + TOTAL_TIMEOUT
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        target = _parse_url(current)
        ip = _resolve(target, deny_networks)
        left = expires - time.monotonic()
        if left <= 0:
            raise FetchError("timed out")
        status, location, content_type, body = _fetch_once(
            target, ip, budget=min(TIMEOUT, left), max_bytes=max_bytes, accept=accept
        )
        if status not in _REDIRECT_STATUSES:
            return FetchResult(content=body, content_type=content_type, final_url=current)
        if not location:
            raise FetchError("redirect without Location")
        try:
            current = urljoin(current, location.strip())
        except ValueError as e:
            raise FetchError("malformed redirect Location") from e
    raise FetchError("too many redirects")
