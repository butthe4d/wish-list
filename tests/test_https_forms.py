"""Forms must work when the app is reached over HTTPS (tunnels, reverse proxies).

Flask-WTF's strict SSL check refuses any HTTPS POST without a same-origin
Referer, so the app must let browsers send one to itself (Referrer-Policy:
same-origin) while still sending nothing to other sites.
"""
import logging
import re

import pytest

from conftest import TEST_SECRET
from wishlist import create_app
from wishlist.config import Config
from wishlist.db import upgrade_db

BASE = "https://wish.example"


@pytest.fixture
def https_app(tmp_path):
    cfg = Config(base_url=BASE, secret_key=TEST_SECRET,
                 database_url=f"sqlite:///{tmp_path / 'h.db'}", data_dir=str(tmp_path))
    upgrade_db(cfg.database_url)
    return create_app(cfg)  # CSRF enabled, like production


def _token(client):
    html = client.get("/", base_url=BASE).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_referrer_policy_is_same_origin(https_app):
    r = https_app.test_client().get("/", base_url=BASE)
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_https_form_with_same_origin_referer_is_accepted(https_app):
    # what a browser sends under Referrer-Policy: same-origin
    c = https_app.test_client()
    r = c.post("/lang", data={"lang": "pl", "next": "/", "csrf_token": _token(c)},
               base_url=BASE, headers={"Referer": BASE + "/"})
    assert r.status_code == 302
    assert any(h.startswith("lang=pl") for h in r.headers.getlist("Set-Cookie"))


def test_csrf_failure_is_logged_without_token(https_app, caplog):
    c = https_app.test_client()
    tok = _token(c)
    caplog.set_level(logging.WARNING)
    c.post("/lang", data={"lang": "pl", "next": "/", "csrf_token": tok}, base_url=BASE)
    assert "CSRF" in caplog.text and "referrer" in caplog.text.lower()
    assert tok not in caplog.text
