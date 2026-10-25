import re

import pytest

from conftest import login, make_user
from wishlist import create_app


@pytest.fixture
def csrf_app(app_config, mailer, sms, app):
    """Same database as `app`, but with CSRF protection switched on."""
    csrf_app = create_app(app_config, mailer=mailer, sms=sms)
    csrf_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return csrf_app


@pytest.fixture
def csrf_client(csrf_app, db):
    _, key = make_user(db, "Ania", "ania@example.com")
    client = csrf_app.test_client()
    login(client, key)
    return client


def test_post_without_token_is_rejected(csrf_client):
    r = csrf_client.post("/items/new", data={"name": "Lamp"})
    assert r.status_code == 400


def test_post_with_token_from_form_succeeds(csrf_client):
    page = csrf_client.get("/items/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = csrf_client.post("/items/new", data={"name": "Lamp", "csrf_token": token})
    assert r.status_code == 302
