import ipaddress
import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import wishlist.fetch as fetch
from wishlist.fetch import FetchError, is_public_address, safe_fetch


# --- local test server -------------------------------------------------------

ROUTES = {}


def route(path):
    def register(fn):
        ROUTES[path] = fn
        return fn

    return register


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        handler = ROUTES.get(self.path.split("?")[0])
        if handler is None:
            self.send_error(404)
            return
        try:
            handler(self)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def _send(h, body, status=200, content_type="text/plain"):
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _redirect(h, location, status=302):
    h.send_response(status)
    h.send_header("Location", location)
    h.send_header("Content-Length", "0")
    h.end_headers()


@route("/a")
def _a(h):
    _redirect(h, "/b")


@route("/b")
def _b(h):
    _send(h, b"hello b", content_type="text/html; charset=utf-8")


@route("/evil")
def _evil(h):
    _redirect(h, "http://10.0.0.5/")


@route("/evil-loopback")
def _evil_loopback(h):
    _redirect(h, f"http://127.0.0.2:{h.server.server_port}/b")


@route("/to-host")
def _to_host(h):
    _redirect(h, f"http://shop.example:{h.server.server_port}/b")


@route("/bad-location")
def _bad_location(h):
    _redirect(h, "http://[::1/")


for _i in range(1, 4):
    def _slow_hop(h, n=_i):
        time.sleep(0.3)
        _redirect(h, f"/slowchain{n + 1}")

    route(f"/slowchain{_i}")(_slow_hop)
route("/slowchain4")(lambda h: _send(h, b"slow chain done"))


@route("/evil-port")
def _evil_port(h):
    _redirect(h, "http://example.com:8080/")


@route("/evil-scheme")
def _evil_scheme(h):
    _redirect(h, "file:///etc/passwd")


for _i in range(1, 5):
    route(f"/chain{_i}")(lambda h, n=_i: _redirect(h, f"/chain{n + 1}", status=(301, 303, 307, 308)[n - 1]))
route("/chain5")(lambda h: _send(h, b"end of chain"))


@route("/no-location")
def _no_location(h):
    h.send_response(302)
    h.send_header("Content-Length", "0")
    h.end_headers()


@route("/big")
def _big(h):
    _send(h, b"x" * 3_000)


@route("/big-chunked")
def _big_chunked(h):
    h.protocol_version = "HTTP/1.1"
    h.send_response(200)
    h.send_header("Transfer-Encoding", "chunked")
    h.send_header("Connection", "close")
    h.end_headers()
    for _ in range(3):
        h.wfile.write(b"3e8\r\n" + b"y" * 1000 + b"\r\n")
    h.wfile.write(b"0\r\n\r\n")


@route("/exact")
def _exact(h):
    _send(h, b"z" * 1000)


@route("/slow")
def _slow(h):
    time.sleep(2)
    _send(h, b"too late")


@route("/drip")
def _drip(h):
    h.send_response(200)
    h.send_header("Content-Length", "100")
    h.end_headers()
    for _ in range(100):
        h.wfile.write(b"d")
        h.wfile.flush()
        time.sleep(0.1)


@route("/drip-headers")
def _drip_headers(h):
    h.wfile.write(b"HTTP/1.0 200 OK\r\n")
    for i in range(100):
        h.wfile.write(b"X-%d: 1\r\n" % i)
        h.wfile.flush()
        time.sleep(0.1)


@route("/missing")
def _missing(h):
    _send(h, b"nope", status=404)


@route("/server-error")
def _server_error(h):
    _send(h, b"boom", status=500)


@route("/echo")
def _echo(h):
    seen = f"{h.path}|{h.headers['Host']}|{h.headers['User-Agent']}|{h.headers['Accept']}"
    _send(h, seen.encode())


@pytest.fixture
def local_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    port = server.server_port
    # The fetcher only allows 80/443; let it reach the test server's port.
    monkeypatch.setattr(fetch, "ALLOWED_PORTS", fetch.ALLOWED_PORTS | {port})
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def allow_loopback(monkeypatch):
    """Treat exactly 127.0.0.1 as public; every other address keeps the real check."""
    real = fetch.is_public_address
    monkeypatch.setattr(
        fetch, "is_public_address", lambda ip: ip == ipaddress.ip_address("127.0.0.1") or real(ip)
    )


# --- address classification --------------------------------------------------


@pytest.mark.parametrize(
    "ip,ok",
    [
        ("8.8.8.8", True),
        ("10.0.0.1", False),
        ("192.168.1.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("::ffff:192.168.1.1", False),
        ("2001:4860:4860::8888", True),
    ],
)
def test_is_public_address(ip, ok):
    assert is_public_address(ipaddress.ip_address(ip)) is ok


@pytest.mark.parametrize(
    "ip,ok",
    [
        ("172.16.0.1", False),
        ("0.0.0.0", False),
        ("0.1.2.3", False),
        ("224.0.0.1", False),
        ("239.255.255.250", False),
        ("240.0.0.1", False),
        ("255.255.255.255", False),
        ("192.0.2.1", False),
        ("198.18.0.1", False),
        ("1.1.1.1", True),
        ("::", False),
        ("fc00::1", False),
        ("fd12:3456::1", False),
        ("fec0::1", False),
        ("ff02::1", False),
        ("2001:db8::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        ("::ffff:8.8.8.8", True),
        ("::127.0.0.1", False),  # IPv4-compatible (deprecated)
        ("::8.8.8.8", False),
        ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
        ("64:ff9b::7f00:1", False),  # NAT64 of 127.0.0.1
        ("64:ff9b::808:808", True),  # NAT64 of 8.8.8.8
        ("64:ff9b:1::808:808", False),  # local-use NAT64 prefix
        ("2002:c0a8:101::1", False),  # 6to4 of 192.168.1.1
        ("2002:808:808::1", False),  # 6to4 is refused outright
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),  # Teredo
    ],
)
def test_is_public_address_more(ip, ok):
    assert is_public_address(ipaddress.ip_address(ip)) is ok


# --- URL validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "http://example.com:8080/",
        "file:///etc/passwd",
        "http://localhost:8000/",
        "http://[::1]/",
    ],
)
def test_refuses_bad_urls(url):
    with pytest.raises(FetchError):
        safe_fetch(url, max_bytes=1000, accept="*/*")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "example.com/x",
        "//example.com/x",
        "javascript:alert(1)",
        "http:///path",
        "http://user@example.com/",
        "http://user:pass@example.com/",
        "http://:pass@example.com/",
        "http://example.com@10.0.0.1/",
        "http://example.com:0/",
        "http://example.com:abc/",
        "http://example.com:99999/",
        "https://example.com:22/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25eth0]/",
        "http://[::ffff:a00:1]/",
        "http://exa mple.com/",
    ],
)
def test_refuses_more_bad_urls(url):
    with pytest.raises(FetchError):
        safe_fetch(url, max_bytes=1000, accept="*/*")


def test_userinfo_refused_before_resolution(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not resolve")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(FetchError):
        safe_fetch("http://user@shop.example/", max_bytes=1000, accept="*/*")


def test_refuses_hostname_resolving_private(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("10.1.2.3", 80))]
    )
    with pytest.raises(FetchError):
        safe_fetch("http://shop.example/", max_bytes=1000, accept="*/*")


def test_refuses_if_any_resolved_address_is_private(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, 1, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET6, 1, 6, "", ("::1", 80, 0, 0)),
        ],
    )
    with pytest.raises(FetchError):
        safe_fetch("http://shop.example/", max_bytes=1000, accept="*/*")


def test_refuses_unresolvable_host(monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(FetchError):
        safe_fetch("http://nonexistent.example/", max_bytes=1000, accept="*/*")


def test_refuses_empty_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(FetchError):
        safe_fetch("http://shop.example/", max_bytes=1000, accept="*/*")


# --- against the local server ---------------------------------------------------


def test_loopback_server_refused_without_patch(local_server):
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/b", max_bytes=1000, accept="*/*")


def test_fetch_ok_and_headers(local_server, allow_loopback):
    result = safe_fetch(local_server + "/echo?q=1", max_bytes=1000, accept="text/html")
    path, host, ua, accept = result.content.decode().split("|")
    assert path == "/echo?q=1"
    assert host == local_server.removeprefix("http://")
    assert ua == "Mozilla/5.0 (compatible; WishlistBot/1.0)"
    assert accept == "text/html"
    assert result.content_type == "text/plain"
    assert result.final_url == local_server + "/echo?q=1"


def test_non_ascii_and_space_in_path_are_percent_encoded(local_server, allow_loopback):
    result = safe_fetch(local_server + "/echo?q=zażółć gęślą", max_bytes=1000, accept="*/*")
    path = result.content.decode().split("|")[0]
    assert path == "/echo?q=za%C5%BC%C3%B3%C5%82%C4%87%20g%C4%99%C5%9Bl%C4%85"


def test_follows_redirect_and_refuses_redirect_to_private(local_server, allow_loopback):
    result = safe_fetch(local_server + "/a", max_bytes=1000, accept="*/*")
    assert result.content == b"hello b"
    assert result.content_type == "text/html; charset=utf-8"
    assert result.final_url.endswith("/b")
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/evil", max_bytes=1000, accept="*/*")


@pytest.mark.parametrize("path", ["/evil-loopback", "/evil-port", "/evil-scheme", "/no-location"])
def test_refuses_bad_redirect_targets(local_server, allow_loopback, path):
    with pytest.raises(FetchError):
        safe_fetch(local_server + path, max_bytes=1000, accept="*/*")


@pytest.mark.parametrize("resolved_ip,ok", [("127.0.0.1", True), ("10.0.0.7", False)])
def test_redirect_hostname_is_resolved_and_checked(local_server, allow_loopback, monkeypatch, resolved_ip, ok):
    port = int(local_server.rsplit(":", 1)[1])
    calls = []

    def fake(host, *a, **k):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    url = local_server + "/to-host"
    if ok:
        assert safe_fetch(url, max_bytes=1000, accept="*/*").final_url == f"http://shop.example:{port}/b"
    else:
        with pytest.raises(FetchError):
            safe_fetch(url, max_bytes=1000, accept="*/*")
    # The literal-IP first hop needs no lookup; the redirect host is looked up exactly once.
    assert calls == ["shop.example"]


def test_three_redirects_allowed(local_server, allow_loopback):
    result = safe_fetch(local_server + "/chain2", max_bytes=1000, accept="*/*")
    assert result.content == b"end of chain"
    assert result.final_url == local_server + "/chain5"


def test_too_many_redirects(local_server, allow_loopback):
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/chain1", max_bytes=1000, accept="*/*")


@pytest.mark.parametrize("path", ["/big", "/big-chunked"])
def test_size_cap(local_server, allow_loopback, path):
    with pytest.raises(FetchError):
        safe_fetch(local_server + path, max_bytes=1000, accept="*/*")


def test_size_exactly_at_cap_is_ok(local_server, allow_loopback):
    assert safe_fetch(local_server + "/exact", max_bytes=1000, accept="*/*").content == b"z" * 1000


def test_timeout(local_server, allow_loopback, monkeypatch):
    monkeypatch.setattr(fetch, "TIMEOUT", 0.5)
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/slow", max_bytes=1000, accept="*/*")


@pytest.mark.parametrize("path", ["/drip", "/drip-headers"])
def test_timeout_is_a_total_deadline(local_server, allow_loopback, monkeypatch, path):
    # Each byte arrives well within the timeout, but the whole response does not.
    monkeypatch.setattr(fetch, "TIMEOUT", 0.5)
    started = time.monotonic()
    with pytest.raises(FetchError):
        safe_fetch(local_server + path, max_bytes=1000, accept="*/*")
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("path", ["/missing", "/server-error"])
def test_non_2xx(local_server, allow_loopback, path):
    with pytest.raises(FetchError):
        safe_fetch(local_server + path, max_bytes=1000, accept="*/*")


def test_connection_refused(monkeypatch, allow_loopback):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(fetch, "ALLOWED_PORTS", fetch.ALLOWED_PORTS | {port})
    with pytest.raises(FetchError):
        safe_fetch(f"http://127.0.0.1:{port}/", max_bytes=1000, accept="*/*")


def test_hostname_resolved_once_and_connection_pinned(local_server, allow_loopback, monkeypatch):
    port = local_server.rsplit(":", 1)[1]
    calls = []

    def fake(host, *a, **k):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", int(port)))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    result = safe_fetch(f"http://shop.example:{port}/echo", max_bytes=1000, accept="*/*")
    assert calls == ["shop.example"]
    assert result.content.decode().split("|")[1] == f"shop.example:{port}"


def test_no_proxy_from_environment(local_server, allow_loopback, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://10.9.9.9:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    assert safe_fetch(local_server + "/b", max_bytes=1000, accept="*/*").content == b"hello b"


def test_constants():
    assert fetch.PAGE_MAX_BYTES == 2_000_000
    assert fetch.IMAGE_MAX_BYTES == 10_000_000
    assert fetch.TIMEOUT == 8
    assert fetch.MAX_REDIRECTS == 3
    assert fetch.TOTAL_TIMEOUT == 20
    assert fetch.ALLOWED_PORTS == frozenset({80, 443})


def test_malformed_redirect_location(local_server, allow_loopback):
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/bad-location", max_bytes=1000, accept="*/*")


def test_watchdog_start_failure_is_fetch_error(local_server, allow_loopback, monkeypatch):
    def fail(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Timer, "start", fail)
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/b", max_bytes=1000, accept="*/*")


def test_total_timeout_across_redirects(local_server, allow_loopback, monkeypatch):
    # Every hop is well within TIMEOUT, but the chain as a whole is not.
    monkeypatch.setattr(fetch, "TOTAL_TIMEOUT", 0.5)
    started = time.monotonic()
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/slowchain1", max_bytes=1000, accept="*/*")
    assert time.monotonic() - started < 1.5


def test_slow_redirect_chain_within_total_timeout(local_server, allow_loopback):
    result = safe_fetch(local_server + "/slowchain1", max_bytes=1000, accept="*/*")
    assert result.content == b"slow chain done"


# --- operator deny-list -------------------------------------------------------------


def _resolve_to(monkeypatch, *addresses):
    calls = []

    def fake(host, port, *a, **k):
        calls.append(host)
        return [
            (socket.AF_INET6 if ":" in a_ else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a_, port))
            for a_ in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


def _connect_forbidden(monkeypatch):
    def boom(self):
        raise AssertionError("must not connect")

    monkeypatch.setattr(fetch._PinnedHTTPConnection, "connect", boom)


@pytest.mark.parametrize(
    "addresses,deny",
    [
        (["8.8.8.8"], ["8.8.8.8/32"]),
        (["1.1.1.1", "8.8.8.8"], ["8.8.8.8/32"]),
        (["2001:4860:4860::8888"], ["2001:4860:4860::/64"]),
        (["::ffff:8.8.8.8"], ["8.8.8.8/32"]),
        (["64:ff9b::808:808"], ["8.8.8.8/32"]),
        (["2001:4860:4860::8888"], ["1.1.1.0/24", "2001:4860:4860::/64"]),
    ],
)
def test_deny_networks_refuse_public_addresses(monkeypatch, addresses, deny):
    _resolve_to(monkeypatch, *addresses)
    _connect_forbidden(monkeypatch)
    with pytest.raises(FetchError):
        safe_fetch("http://shop.example/", max_bytes=1000, accept="*/*",
                   deny_networks=[ipaddress.ip_network(n) for n in deny])


def test_deny_networks_apply_to_ip_literal_urls(monkeypatch):
    _connect_forbidden(monkeypatch)
    with pytest.raises(FetchError):
        safe_fetch("http://8.8.8.8/", max_bytes=1000, accept="*/*",
                   deny_networks=[ipaddress.ip_network("8.8.8.8/32")])


def test_deny_networks_do_not_block_other_addresses(local_server, allow_loopback):
    deny = [ipaddress.ip_network("8.8.8.8/32"), ipaddress.ip_network("2001:4860:4860::/64")]
    result = safe_fetch(local_server + "/b", max_bytes=1000, accept="*/*", deny_networks=deny)
    assert result.content == b"hello b"


def test_deny_networks_apply_to_redirect_hops(local_server, allow_loopback, monkeypatch):
    calls = _resolve_to(monkeypatch, "8.8.8.8")
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/to-host", max_bytes=1000, accept="*/*",
                   deny_networks=[ipaddress.ip_network("8.8.8.8/32")])
    assert calls == ["shop.example"]


def test_deny_networks_can_refuse_first_hop(local_server, allow_loopback):
    with pytest.raises(FetchError):
        safe_fetch(local_server + "/b", max_bytes=1000, accept="*/*",
                   deny_networks=[ipaddress.ip_network("127.0.0.1/32")])


# --- HTTPS: pinned IP, SNI and certificate checked against the hostname ----------


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=shop.example", "-addext", "subjectAltName=DNS:shop.example",
         "-keyout", str(key), "-out", str(cert)],
        check=True,
        capture_output=True,
    )
    server_names = []
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    ctx.sni_callback = lambda sslsock, name, _ctx: server_names.append(name)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.handle_error = lambda *a: None  # failed handshakes are expected in some tests
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    port = server.server_port
    monkeypatch.setattr(fetch, "ALLOWED_PORTS", fetch.ALLOWED_PORTS | {port})

    resolved = []

    def fake_getaddrinfo(host, *a, **k):
        resolved.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    yield {"port": port, "cert": cert, "server_names": server_names, "resolved": resolved}
    server.shutdown()
    server.server_close()


def _trust(monkeypatch, cert):
    monkeypatch.setattr(fetch, "_tls_context", lambda: ssl.create_default_context(cafile=str(cert)))


def test_https_pinned_with_sni_and_verified_hostname(tls_server, allow_loopback, monkeypatch):
    _trust(monkeypatch, tls_server["cert"])
    port = tls_server["port"]
    result = safe_fetch(f"https://shop.example:{port}/echo", max_bytes=1000, accept="*/*")
    assert result.content.decode().split("|")[1] == f"shop.example:{port}"
    assert tls_server["server_names"] == ["shop.example"]
    assert tls_server["resolved"] == ["shop.example"]


def test_https_hostname_mismatch_refused(tls_server, allow_loopback, monkeypatch):
    _trust(monkeypatch, tls_server["cert"])
    with pytest.raises(FetchError):
        safe_fetch(f"https://other.example:{tls_server['port']}/b", max_bytes=1000, accept="*/*")


def test_https_untrusted_certificate_refused(tls_server, allow_loopback):
    with pytest.raises(FetchError):
        safe_fetch(f"https://shop.example:{tls_server['port']}/b", max_bytes=1000, accept="*/*")


def test_default_tls_context_verifies():
    ctx = fetch._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
