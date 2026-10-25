from conftest import TEST_SECRET, login, make_user

from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist, delete_item
from wishlist.keys import share_key
from wishlist.models import Reservation
from wishlist.sharing import regenerate_share_key


def _key(wl):
    return share_key(TEST_SECRET, wl.id, wl.share_version)


def _guest(app, wl):
    c = app.test_client()
    r = c.get(f"/s/{_key(wl)}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/shared/{wl.id}")
    return c


def _url(wl, item, action):
    return f"/shared/{wl.id}/items/{item.id}/{action}"


def test_reserve_flow_hides_name_from_others(app, db, item_in_list):
    item, wl = item_in_list
    c1 = _guest(app, wl)
    assert "Lamp" in c1.get(f"/shared/{wl.id}").text
    r = c1.post(_url(wl, item, "reserve"), data={"name": "Mom"})
    assert r.status_code == 302
    page = c1.get(f"/shared/{wl.id}")
    assert "Reserved by you" in page.text
    assert page.headers["Cache-Control"] == "no-store"
    c2 = _guest(app, wl)
    html = c2.get(f"/shared/{wl.id}").text
    assert "Reserved" in html and "Mom" not in html and "Reserved by you" not in html
    assert "Ania" in html


def test_guest_cookie_flags(app, item_in_list):
    _, wl = item_in_list
    c = app.test_client()
    cookie = next(h for h in c.get(f"/s/{_key(wl)}").headers.getlist("Set-Cookie") if h.startswith("guest="))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Max-Age=31536000" in cookie


def test_cancel_by_other_guest_does_nothing(app, db, item_in_list):
    item, wl = item_in_list
    c1, c2 = _guest(app, wl), _guest(app, wl)
    c1.post(_url(wl, item, "reserve"), data={"name": "Mom"})
    c2.post(_url(wl, item, "cancel"))
    assert db.query(Reservation).count() == 1
    c1.post(_url(wl, item, "cancel"))
    assert db.query(Reservation).count() == 0


def test_stale_reserve_shows_message_not_500(app, db, item_in_list):
    item, wl = item_in_list
    c1, c2 = _guest(app, wl), _guest(app, wl)
    c1.post(_url(wl, item, "reserve"), data={"name": "Mom"})
    r = c2.post(_url(wl, item, "reserve"), data={"name": "Dad"})
    assert r.status_code in (200, 409)
    assert "just reserved" in r.text and "Mom" not in r.text
    assert db.query(Reservation).one().reserver_name == "Mom"


def test_regenerate_invalidates_links_and_sessions(app, db, item_in_list):
    _, wl = item_in_list
    c = _guest(app, wl)
    old = _key(wl)
    regenerate_share_key(db, wl)
    assert app.test_client().get(f"/s/{old}").status_code == 404
    assert c.get(f"/shared/{wl.id}").status_code == 404
    assert _guest(app, wl).get(f"/shared/{wl.id}").status_code == 200


def test_no_share_session_is_404(app, item_in_list):
    _, wl = item_in_list
    assert app.test_client().get(f"/shared/{wl.id}").status_code == 404
    assert app.test_client().get("/s/garbage").status_code == 404
    assert app.test_client().get("/s/%C3%A9.x").status_code == 404


def test_item_must_be_in_the_shared_list(app, db, user, item_in_list):
    _, a = item_in_list
    b = create_wishlist(db, user, name="B", description=None)
    other_item = create_item(db, user, name="Vase", description=None, link=None)
    add_items_to_wishlist(db, b, [other_item.id])
    c = _guest(app, a)
    assert c.post(_url(a, other_item, "reserve"), data={"name": "Mom"}).status_code == 404
    assert c.post(_url(a, other_item, "cancel")).status_code == 404
    assert c.get(f"/shared/{b.id}").status_code == 404
    assert db.query(Reservation).count() == 0


def test_deleted_item_hidden_and_not_reservable(app, db, item_in_list):
    item, wl = item_in_list
    c = _guest(app, wl)
    delete_item(db, item)
    assert "Lamp" not in c.get(f"/shared/{wl.id}").text
    assert c.post(_url(wl, item, "reserve"), data={"name": "Mom"}).status_code == 404
    assert c.post(_url(wl, item, "cancel")).status_code == 404


def test_owner_redirected_to_owner_view(app, db, user, item_in_list):
    from wishlist.keys import hash_key

    item, wl = item_in_list
    key = "owner-key"
    user.key_hash = hash_key(key)
    db.commit()
    c = app.test_client()
    login(c, key)
    r = c.get(f"/s/{_key(wl)}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/lists/{wl.id}")
    c2 = _guest(app, wl)
    # owner with a share session as well (e.g. link opened before login)
    with c.session_transaction() as sess:
        sess["shares"] = {str(wl.id): wl.share_version}
    r = c.get(f"/shared/{wl.id}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/lists/{wl.id}")
    assert c.post(_url(wl, item, "reserve"), data={"name": "x"}).status_code == 302
    assert db.query(Reservation).count() == 0
    del c2


def test_non_owner_member_gets_guest_view(app, db, item_in_list):
    item, wl = item_in_list
    _, key = make_user(db, "Admin", "admin@x.pl", is_admin=True)
    c = app.test_client()
    login(c, key)
    assert c.get(f"/s/{_key(wl)}").headers["Location"].endswith(f"/shared/{wl.id}")
    assert "Reserve" in c.get(f"/shared/{wl.id}").text
    with c.session_transaction() as sess:
        assert "uid" in sess and sess["shares"] == {str(wl.id): wl.share_version}


def test_blank_name_shows_error(app, db, item_in_list):
    item, wl = item_in_list
    c = _guest(app, wl)
    r = c.post(_url(wl, item, "reserve"), data={"name": "  "})
    assert r.status_code in (200, 400)
    assert "Please enter your name." in r.text
    assert db.query(Reservation).count() == 0


def test_exception_log_has_no_share_key(app, client, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("wishlist.sharing.open_share", boom)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    key = "1.abcDEF123_-abcDEF123_-abcDEF123_-abcDEF123_-"
    with caplog.at_level("ERROR", logger=app.logger.name):
        assert client.get(f"/s/{key}").status_code == 500
    messages = [r.getMessage() for r in caplog.records]
    assert any("/s/***" in m for m in messages)
    assert not any(key in m for m in messages)
