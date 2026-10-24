from conftest import login, make_user

from wishlist.catalog import create_item, delete_item
from wishlist.models import Item, Reservation, Wishlist


def _login(client, db, name="Ania", email="a@x.pl"):
    u, key = make_user(db, name, email)
    login(client, key)
    return u


def test_create_item_appears_on_home(client, db):
    u = _login(client, db)
    w = Wishlist(owner_id=u.id, name="Birthday")
    db.add(w)
    db.commit()
    r = client.post(
        "/items/new",
        data={"name": "Lamp", "description": "Warm", "link": "shop.pl/lamp", "wishlist_ids": [w.id]},
    )
    assert r.status_code == 302
    html = client.get("/").text
    assert "Lamp" in html and "https://shop.pl/lamp" in html and "Birthday" in html
    assert "1 item" in html
    assert 'rel="noopener noreferrer"' in html


def test_form_shows_checkboxes_only_with_lists(client, db):
    u = _login(client, db)
    assert 'type="checkbox"' not in client.get("/items/new").text
    db.add(Wishlist(owner_id=u.id, name="Birthday"))
    db.commit()
    assert 'type="checkbox"' in client.get("/items/new").text


def test_empty_name_shows_error(client, db):
    _login(client, db)
    r = client.post("/items/new", data={"name": "  ", "description": "d"})
    assert r.status_code == 200
    assert "Name is required." in r.text
    assert db.query(Item).count() == 0


def test_bad_link_shows_error_and_keeps_input(client, db):
    _login(client, db)
    r = client.post("/items/new", data={"name": "Lamp", "link": "javascript:alert(1)"})
    assert r.status_code == 200
    assert "must start with http" in r.text
    assert "Lamp" in r.text
    assert db.query(Item).count() == 0


def test_edit_item(client, db):
    u = _login(client, db)
    item = create_item(db, u, name="Lamp", description=None, link=None)
    assert "Lamp" in client.get(f"/items/{item.id}/edit").text
    r = client.post(f"/items/{item.id}/edit", data={"name": "Big lamp"})
    assert r.status_code == 302
    assert "Big lamp" in client.get("/").text


def test_edit_error_rerenders(client, db):
    u = _login(client, db)
    item = create_item(db, u, name="Lamp", description=None, link=None)
    r = client.post(f"/items/{item.id}/edit", data={"name": ""})
    assert r.status_code == 200 and "Name is required." in r.text


def test_other_members_item_is_404(client, db, other_user):
    _login(client, db)
    item = create_item(db, other_user, name="Secret", description=None, link=None)
    assert client.get(f"/items/{item.id}/edit").status_code == 404
    assert client.post(f"/items/{item.id}/edit", data={"name": "x"}).status_code == 404
    assert client.post(f"/items/{item.id}/delete").status_code == 404
    assert "Secret" not in client.get("/").text


def test_foreign_list_id_is_404(client, db, other_user):
    _login(client, db)
    w = Wishlist(owner_id=other_user.id, name="Theirs")
    db.add(w)
    db.commit()
    r = client.post("/items/new", data={"name": "x", "wishlist_ids": [w.id]})
    assert r.status_code == 404
    assert db.query(Item).count() == 0
    assert client.post("/items/new", data={"name": "x", "wishlist_ids": ["abc"]}).status_code == 404


def test_delete_item(client, db):
    u = _login(client, db)
    item = create_item(db, u, name="Lamp", description=None, link=None)
    assert client.post(f"/items/{item.id}/delete").status_code == 302
    assert "Lamp" not in client.get("/").text


def test_soft_deleted_item_not_listed_and_no_reservation_info(client, db):
    u = _login(client, db)
    gone = create_item(db, u, name="Gone", description=None, link=None)
    kept = create_item(db, u, name="Kept", description=None, link=None)
    for it in (gone, kept):
        db.add(
            Reservation(
                item_id=it.id, reserver_name="Zosia", guest_token_hash="h", via_wishlist_name="L"
            )
        )
    db.commit()
    db.refresh(gone)
    delete_item(db, gone)
    html = client.get("/").text
    assert "Gone" not in html and "Kept" in html
    assert "Zosia" not in html and "eserved" not in html
    assert "Zosia" not in client.get(f"/items/{kept.id}/edit").text


def test_login_redirects_to_home(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    r = client.get(f"/u/{key}")
    assert r.headers["Location"].endswith("/")


def test_language_switcher_next_is_root_on_post_error(client, db):
    _login(client, db)
    r = client.post("/items/new", data={"name": ""})
    assert 'name="next" value="/"' in r.text
    r = client.get("/items/new")
    assert 'name="next" value="/items/new"' in r.text


def test_out_of_range_list_id_is_404(client, db):
    _login(client, db)
    for bad in ("9" * 30, "-1", "0"):
        r = client.post("/items/new", data={"name": "x", "wishlist_ids": [bad]})
        assert r.status_code == 404
