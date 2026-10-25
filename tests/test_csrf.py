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


def test_post_without_token_is_rejected(csrf_client, db):
    from wishlist.models import Item

    r = csrf_client.post("/items/new", data={"name": "Lamp"})
    assert r.status_code == 302  # CSRF handler redirects, no raw 400
    assert db.query(Item).count() == 0


def test_post_with_token_from_form_succeeds(csrf_client):
    page = csrf_client.get("/items/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = csrf_client.post("/items/new", data={"name": "Lamp", "csrf_token": token})
    assert r.status_code == 302


def test_csrf_token_has_no_time_limit(csrf_app):
    assert csrf_app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_invalid_token_redirects_with_localized_flash(csrf_client):
    r = csrf_client.post(
        "/items/new", data={"name": "Lamp", "csrf_token": "stale"},
        headers={"Referer": "/items/new"},
    )
    assert r.status_code == 302 and r.headers["Location"] == "/items/new"
    page = csrf_client.get("/").get_data(as_text=True)
    assert "This page expired, please try again." in page


def test_csrf_failure_message_is_polish(csrf_app):
    guest = csrf_app.test_client()
    pl = {"Accept-Language": "pl"}
    assert guest.post("/items/new", data={"name": "x"}, headers=pl).status_code == 302
    assert "Ta strona wygasła, spróbuj ponownie." in guest.get("/", headers=pl).get_data(as_text=True)


@pytest.mark.parametrize("referer", ["https://evil.example/x", "//evil.example", "", "/\\evil"])
def test_csrf_failure_never_redirects_off_site(csrf_client, referer):
    r = csrf_client.post("/items/new", data={}, headers={"Referer": referer})
    assert r.status_code == 302 and r.headers["Location"] == "/"
