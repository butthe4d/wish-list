import ipaddress
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import wishlist.fetch as fetch
from wishlist import create_app
from wishlist.config import Config
from sqlalchemy.orm import sessionmaker

from wishlist.db import upgrade_db
from wishlist.models import User

from fakes import RecordingMailer, RecordingSms


TEST_SECRET = "test-secret-" + "x" * 32


@pytest.fixture(scope="session", autouse=True)
def compiled_translations():
    root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", "wishlist/translations"],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def app_config(tmp_path):
    return Config(
        base_url="https://wish.example",
        secret_key=TEST_SECRET,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=str(tmp_path),
    )


@pytest.fixture
def mailer():
    return RecordingMailer()


@pytest.fixture
def sms():
    return RecordingSms()


@pytest.fixture
def app(app_config, mailer, sms):
    """Senders are injected recorders. To test a disabled channel, build an app with
    create_app(app_config, sms=DISABLED) (see tests/test_share_send.py)."""
    upgrade_db(app_config.database_url)
    app = create_app(app_config, mailer=mailer, sms=sms)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """A session of its own, like a separate process: requests use their own scoped session,
    so a domain function that forgets to commit is caught by the tests."""
    session = sessionmaker(bind=app.extensions["wishlist_engine"], expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(name="Ania", email="ania@example.com", language="en", is_admin=False, key_hash="0" * 64)
    db.add(u)
    db.flush()
    return u


def make_user(db, name, email, *, is_admin=False, language="en"):
    from wishlist.keys import hash_key, new_key

    key = new_key()
    u = User(name=name, email=email, language=language, is_admin=is_admin, key_hash=hash_key(key))
    db.add(u)
    db.commit()
    return u, key


def login(client, key):
    r = client.get(f"/u/{key}")
    assert r.status_code == 302


@pytest.fixture
def other_user(db):
    return make_user(db, "Basia", "basia@example.com")[0]


@pytest.fixture
def item_in_list(db, user):
    from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist

    wl = create_wishlist(db, user, name="Birthday", description=None)
    item = create_item(db, user, name="Lamp", description=None, link=None)
    add_items_to_wishlist(db, wl, [item.id])
    return item, wl


@pytest.fixture
def admin_client(app, db):
    _, key = make_user(db, "Maciej", "maciej@example.com", is_admin=True)
    c = app.test_client()
    login(c, key)
    return c


@pytest.fixture
def catalog_mailer(monkeypatch, mailer):
    """Catalog functions look up the mailer via the Flask app; tests that call them directly
    (outside a request) get the recording mailer through this."""
    monkeypatch.setattr("wishlist.catalog.get_mailer", lambda: mailer)
    monkeypatch.setattr("wishlist.catalog.get_base_url", lambda: "https://wish.example")
    return mailer


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
