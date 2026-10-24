import subprocess
import sys
from pathlib import Path

import pytest

from wishlist import create_app
from wishlist.config import Config
from sqlalchemy.orm import sessionmaker

from wishlist.db import upgrade_db
from wishlist.models import User


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
        secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def app(app_config):
    upgrade_db(app_config.database_url)
    app = create_app(app_config)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """A session of its own, like a separate process: requests use their own scoped session,
    so a domain function that forgets to commit is caught by the tests."""
    session = sessionmaker(bind=app.extensions["wishlist_engine"])()
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
