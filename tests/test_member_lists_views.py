from conftest import TEST_SECRET, login, make_user

from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist
from wishlist.keys import share_key
from wishlist.models import Item, Reservation, Wishlist


def _login(client, db, name="Ania", email="a@x.pl"):
    u, key = make_user(db, name, email)
    login(client, key)
    return u


def _list(db, u, name="Birthday"):
    return create_wishlist(db, u, name=name, description=None)


def _item(db, u, name="Lamp"):
    return create_item(db, u, name=name, description=None, link=None)


def test_create_list_appears_on_home_and_links(client, db):
    u = _login(client, db)
    r = client.post("/lists", data={"name": "Birthday", "description": "Soon"})
    assert r.status_code == 302
    wl = db.query(Wishlist).one()
    assert wl.owner_id == u.id and wl.description == "Soon"
    assert r.headers["Location"].endswith(f"/lists/{wl.id}")
    html = client.get("/").text
    assert "Birthday" in html and f"/lists/{wl.id}" in html


def test_create_list_empty_name_shows_error_on_home(client, db):
    _login(client, db)
    r = client.post("/lists", data={"name": " "})
    assert r.status_code == 200
    assert "Name is required." in r.text
    assert db.query(Wishlist).count() == 0


def test_owner_page_shows_items_share_link_and_picker(client, db, app):
    u = _login(client, db)
    wl = _list(db, u)
    inside, outside = _item(db, u, "Inside"), _item(db, u, "Outside")
    add_items_to_wishlist(db, wl, [inside.id])
    html = client.get(f"/lists/{wl.id}").text
    key = share_key(TEST_SECRET, wl.id, wl.share_version)
    assert f"https://wish.example/s/{key}" in html
    assert "/s/" in html
    assert "Inside" in html and "Outside" in html
    # picker offers only the item that is not yet in the list
    assert f'value="{outside.id}"' in html and f'value="{inside.id}"' not in html


def test_add_and_remove_item(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    a, b = _item(db, u, "A"), _item(db, u, "B")
    r = client.post(f"/lists/{wl.id}/items", data={"item_id": [a.id, b.id]})
    assert r.status_code == 302
    db.expire_all()
    assert {i.id for i in db.get(Wishlist, wl.id).items} == {a.id, b.id}
    r = client.post(f"/lists/{wl.id}/items/{a.id}/remove")
    assert r.status_code == 302
    db.expire_all()
    assert [i.id for i in db.get(Wishlist, wl.id).items] == [b.id]


def test_add_with_nothing_selected_is_noop(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    assert client.post(f"/lists/{wl.id}/items", data={}).status_code == 302


def test_add_other_members_item_404(client, db, other_user):
    u = _login(client, db)
    wl = _list(db, u)
    theirs = _item(db, other_user)
    assert client.post(f"/lists/{wl.id}/items", data={"item_id": [theirs.id]}).status_code == 404
    db.expire_all()
    assert db.get(Wishlist, wl.id).items == []


def test_add_bad_item_ids_404(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    for bad in ("abc", str(2**63), "0", "-1"):
        r = client.post(f"/lists/{wl.id}/items", data={"item_id": [bad]})
        assert r.status_code == 404


def test_remove_foreign_item_404(client, db, other_user):
    u = _login(client, db)
    wl = _list(db, u)
    theirs = _item(db, other_user)
    assert client.post(f"/lists/{wl.id}/items/{theirs.id}/remove").status_code == 404


def test_other_members_list_404_everywhere(client, db, other_user):
    _login(client, db)
    wl = _list(db, other_user)
    assert client.get(f"/lists/{wl.id}").status_code == 404
    assert client.post(f"/lists/{wl.id}/edit", data={"name": "x"}).status_code == 404
    assert client.post(f"/lists/{wl.id}/delete").status_code == 404
    assert client.post(f"/lists/{wl.id}/items", data={}).status_code == 404
    assert client.post(f"/lists/{wl.id}/items/1/remove").status_code == 404
    db.expire_all()
    assert db.get(Wishlist, wl.id) is not None


def test_logged_out_is_rejected(client, db, user):
    wl = _list(db, user)
    assert client.get(f"/lists/{wl.id}").status_code == 401
    assert client.post("/lists", data={"name": "x"}).status_code == 401


def test_nonexistent_and_huge_list_ids_404(client, db):
    _login(client, db)
    assert client.get("/lists/9999").status_code == 404
    assert client.get(f"/lists/{2**64}").status_code == 404


def test_rename_list(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    r = client.post(f"/lists/{wl.id}/edit", data={"name": "Xmas", "description": "d"})
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Wishlist, wl.id).name == "Xmas"
    assert "Xmas" in client.get(f"/lists/{wl.id}").text


def test_rename_invalid_shows_error_and_keeps_name(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    r = client.post(f"/lists/{wl.id}/edit", data={"name": " "})
    assert r.status_code == 200
    assert "Name is required." in r.text
    db.expire_all()
    assert db.get(Wishlist, wl.id).name == "Birthday"


def test_delete_list_keeps_items(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    item = _item(db, u)
    add_items_to_wishlist(db, wl, [item.id])
    wl_id, item_id = wl.id, item.id
    r = client.post(f"/lists/{wl_id}/delete")
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Wishlist, wl_id) is None
    assert db.get(Item, item_id) is not None
    assert "Lamp" in client.get("/").text


def test_owner_page_hides_reservation_state(client, db):
    u = _login(client, db)
    wl = _list(db, u)
    item = _item(db, u, "Lamp")
    add_items_to_wishlist(db, wl, [item.id])
    db.add(
        Reservation(
            item_id=item.id, reserver_name="Mom", guest_token_hash="h",
            via_wishlist_id=wl.id, via_wishlist_name="Birthday",
        )
    )
    db.commit()
    r = client.get(f"/lists/{wl.id}")
    assert r.status_code == 200
    assert "Lamp" in r.text and "/s/" in r.text
    assert "Mom" not in r.text and "reserved" not in r.text.lower()
