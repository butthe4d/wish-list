import pytest

from wishlist import create_app
from wishlist.config import Config


@pytest.fixture
def app_config(tmp_path):
    return Config(
        base_url="https://wish.example",
        secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def app(app_config):
    app = create_app(app_config)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()
