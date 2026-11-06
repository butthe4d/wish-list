import re

import pytest

from conftest import TEST_SECRET, login
from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist, delete_item
from wishlist.keys import hash_key, share_key
from wishlist.models import Reservation
from wishlist.sharing import regenerate_share_key

pytestmark = pytest.mark.usefixtures("catalog_mailer")


def _guest(app, wl):
    c = app.test_client()
    r = c.get(f"/s/{share_key(TEST_SECRET, wl.id, wl.share_version)}")
    assert r.status_code == 302
    return c


def _detail(wl, item):
    return f"/shared/{wl.id}/items/{item.id}"


@pytest.fixture
def three_items(db, user):
    wl = create_wishlist(db, user, name="Xmas", description=None)
    items = [
        create_item(db, user, name=n, description=None, link=None) for n in ("Lamp", "Book", "Mug")
    ]
    add_items_to_wishlist(db, wl, [i.id for i in items])
    wl.theme = "christmas"
    db.commit()
    return wl, items


def test_grid_shows_tiles_with_states(app, three_items):
    wl, (lamp, book, mug) = three_items
    me, other = _guest(app, wl), _guest(app, wl)
    other.post(_detail(wl, book) + "/reserve", data={"name": "Grandma"})
    me.post(_detail(wl, mug) + "/reserve", data={"name": "Mom"})
    r = me.get(f"/shared/{wl.id}")
    html = r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert 'data-theme="christmas"' in html
    tiles = re.findall(r'<a class="tile[^"]*"[^>]*>.*?</a>', html, flags=re.S)
    assert len(tiles) == 3
    by_name = {n: next(t for t in tiles if n in t) for n in ("Lamp", "Book", "Mug")}
    assert f'href="{_detail(wl, lamp)}"' in by_name["Lamp"] and "data-detail" in by_name["Lamp"]
    assert "is-available" in by_name["Lamp"]
    assert "Reserved<" in by_name["Book"] and "is-reserved" in by_name["Book"]
    assert "Your reservation" in by_name["Mug"] and "is-mine" in by_name["Mug"]
    assert "Grandma" not in html and "Mom" not in html
    assert (
        "Gifts Ania is wishing for. Tap a gift to see more — Ania won't see who picked what."
        in html
    )


def test_grid_hint_in_polish(app, three_items):
    wl, _ = three_items
    c = _guest(app, wl)
    html = c.get(f"/shared/{wl.id}", headers={"Accept-Language": "pl"}).text
    assert "Prezenty, o których marzy Ania." in html and "Lista życzeń" in html


def test_detail_available_shows_reserve_form(app, db, item_in_list):
    item, wl = item_in_list
    item.link = "https://shop.example.com/lamp"
    item.description = "Warm light"
    db.commit()
    r = _guest(app, wl).get(_detail(wl, item))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    html = r.text
    assert 'class="item-detail' in html and "Lamp" in html and "Warm light" in html
    assert "Who's buying this?" in html and "I'll get it" in html
    assert "Ania won't see it's you." in html
    assert f'action="{_detail(wl, item)}/reserve"' in html
    assert 'name="csrf_token"' in html
    assert re.search(
        r'<a [^>]*href="https://shop.example.com/lamp"[^>]*rel="noopener noreferrer" target="_blank"',
        html,
    )
    assert "See in shop" in html
    assert f'href="/shared/{wl.id}"' in html  # way back to the grid without JS


def test_detail_mine_and_taken(app, db, item_in_list):
    item, wl = item_in_list
    me, other = _guest(app, wl), _guest(app, wl)
    me.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    mine = me.get(_detail(wl, item)).text
    assert "Your reservation" in mine and f'action="{_detail(wl, item)}/cancel"' in mine
    assert "Cancel reservation" in mine
    taken = other.get(_detail(wl, item)).text
    assert "Someone is already getting this" in taken
    assert "<form" not in taken.split('class="item-detail', 1)[1].split("</main>")[0]
    assert "Mom" not in taken and "Your reservation" not in taken


def test_reserve_from_detail_redirects_to_grid(app, db, item_in_list):
    item, wl = item_in_list
    c = _guest(app, wl)
    r = c.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/shared/{wl.id}")
    assert db.query(Reservation).one().reserver_name == "Mom"
    r = c.post(_detail(wl, item) + "/cancel")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/shared/{wl.id}")
    assert db.query(Reservation).count() == 0


def test_blank_name_rerenders_detail_400(app, db, item_in_list):
    item, wl = item_in_list
    r = _guest(app, wl).post(_detail(wl, item) + "/reserve", data={"name": "  "})
    assert r.status_code == 400
    assert 'class="item-detail' in r.text and "Please enter your name." in r.text
    assert 'aria-invalid="true"' in r.text
    assert db.query(Reservation).count() == 0


def test_already_reserved_rerenders_detail_409(app, db, item_in_list):
    item, wl = item_in_list
    c1, c2 = _guest(app, wl), _guest(app, wl)
    c1.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    r = c2.post(_detail(wl, item) + "/reserve", data={"name": "Dad"})
    assert r.status_code == 409
    assert 'class="item-detail' in r.text and "just reserved" in r.text
    assert "Someone is already getting this" in r.text and "Mom" not in r.text


def test_double_submit_of_own_reservation_returns_to_grid(app, db, item_in_list):
    item, wl = item_in_list
    c = _guest(app, wl)
    c.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    r = c.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/shared/{wl.id}")
    assert db.query(Reservation).one().reserver_name == "Mom"


def test_detail_page_heading_is_h1(app, item_in_list):
    item, wl = item_in_list
    html = _guest(app, wl).get(_detail(wl, item)).text
    assert re.search(r'<h1 id="detail-title">Lamp</h1>', html)


def test_detail_access_rules(app, db, user, item_in_list):
    item, wl = item_in_list
    other_list = create_wishlist(db, user, name="Other", description=None)
    vase = create_item(db, user, name="Vase", description=None, link=None)
    add_items_to_wishlist(db, other_list, [vase.id])
    c = _guest(app, wl)
    assert c.get(_detail(wl, vase)).status_code == 404
    assert c.get(f"/shared/{wl.id}/items/99999").status_code == 404
    assert app.test_client().get(_detail(wl, item)).status_code == 404  # no share session

    owner = app.test_client()
    user.key_hash = hash_key("owner-key")
    db.commit()
    login(owner, "owner-key")
    with owner.session_transaction() as sess:
        sess["shares"] = {str(wl.id): wl.share_version}
    r = owner.get(_detail(wl, item))
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/lists/{wl.id}")

    c.post(_detail(wl, item) + "/reserve", data={"name": "Mom"})
    delete_item(db, item)  # reserved, so soft-deleted
    assert c.get(_detail(wl, item)).status_code == 404

    regenerate_share_key(db, wl)
    assert c.get(_detail(wl, vase)).status_code == 404


def test_detail_revoked_share_404(app, db, item_in_list):
    item, wl = item_in_list
    c = _guest(app, wl)
    assert c.get(_detail(wl, item)).status_code == 200
    regenerate_share_key(db, wl)
    assert c.get(_detail(wl, item)).status_code == 404


def test_picture_on_tile_and_detail(app, db, item_in_list):
    item, wl = item_in_list
    item.image = "abc123.jpg"
    db.commit()
    c = _guest(app, wl)
    # the tile already names the gift in text, so its picture is decorative
    grid = c.get(f"/shared/{wl.id}").text
    assert re.search(r'<img src="/images/abc123.jpg" alt="" loading="lazy">', grid)
    detail = c.get(_detail(wl, item)).text
    assert re.search(r'<img src="/images/abc123.jpg" alt="Lamp" loading="lazy">', detail)


def test_detail_dialog_wiring(app, item_in_list):
    _, wl = item_in_list
    html = _guest(app, wl).get(f"/shared/{wl.id}").text
    assert '<dialog id="detail"' in html and "dialog.js" in html
    js = app.test_client().get("/static/dialog.js")
    assert js.status_code == 200 and b"showModal" in js.data and b".item-detail" in js.data
