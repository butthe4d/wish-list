import pytest

from wishlist import create_app
from wishlist.config import Config
from wishlist.db import get_session, upgrade_db
from wishlist.models import User


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
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_session()


@pytest.fixture
def user(db):
    u = User(name="Ania", email="ania@example.com", language="en", is_admin=False, key_hash="0" * 64)
    db.add(u)
    db.flush()
    return u
