import io
import re

import pytest
from PIL import Image

import wishlist.pictures as pictures
from conftest import _send, login, make_user, route
from wishlist import images
from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist, set_item_image
from wishlist.models import Item, Reservation, Wishlist

pytestmark = pytest.mark.usefixtures("catalog_mailer")


def _jpeg(color="red", size=(300, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    pictures._fetch_log.clear()
    yield
    pictures._fetch_log.clear()


@pytest.fixture
def member_and_key(db):
    return make_user(db, "Ania Nowak", "ania@x.pl")


@pytest.fixture
def member(member_and_key):
    return member_and_key[0]


@pytest.fixture
def member_client(app, member_and_key):
    c = app.test_client()
    login(c, member_and_key[1])
    return c


def _radios(html, name="picture"):
    return re.findall(r'<input type="radio" name="%s" value="([^"]*)"([^>]*)>' % name, html)


def _checked(html, name="picture"):
    return [v for v, attrs in _radios(html, name) if "checked" in attrs]


# --- picture picker ------------------------------------------------------------------


def test_form_puts_shop_link_first_and_has_find_button(member_client):
    html = member_client.get("/items/new").text
    form = html.split("<form", 1)[1]
    assert form.index('name="link"') < form.index('name="name"') < form.index('name="description"')
    assert re.search(r'<button[^>]*name="action" value="find"[^>]*>\s*Find pictures', html)
    assert {v for v, _ in _radios(html)} == {"url", "none"}
    assert _checked(html) == ["none"]
    assert 'name="image_url"' in html


def test_find_pictures_shows_candidates(member_client, local_server, allow_loopback):
    page = (
        '<html><head><meta property="og:image" content="/a.jpg"></head><body>'
        '<img src="/b.jpg"><img src="/c.jpg"><img src="/d.jpg"></body></html>'
    ).encode()
    route("/product")(lambda h: _send(h, page, content_type="text/html; charset=utf-8"))
    for n, color in zip("abcd", ("red", "green", "blue", "black")):
        route(f"/{n}.jpg")(lambda h, c=color: _send(h, _jpeg(c), content_type="image/jpeg"))
    r = member_client.post(
        "/items/new",
        data={"link": local_server + "/product", "name": "Lamp", "description": "Warm",
              "picture": "none", "action": "find"},
    )
    assert r.status_code == 200
    html = r.text
    temps = [v for v, _ in _radios(html) if v.startswith("temp:")]
    assert len(temps) == 3
    for value in temps:
        token = value.split(":", 1)[1]
        assert f'src="/images/tmp/{token}"' in html
        assert member_client.get(f"/images/tmp/{token}").status_code == 200
    assert _checked(html) == [temps[0]]  # a new item takes the first picture by default
    assert {"url", "none"} <= {v for v, _ in _radios(html)}
    assert 'value="Lamp"' in html and "Warm" in html  # other fields kept
    assert pictures._fetch_log  # counted as a search


def test_find_pictures_failure_message(member_client, monkeypatch):
    def boom(url, limit=3):
        raise pictures.PictureError("blocked")

    monkeypatch.setattr(pictures, "find_pictures", boom)
    r = member_client.post(
        "/items/new", data={"link": "https://shop.example/x", "name": "Lamp", "action": "find"}
    )
    assert r.status_code == 200
    assert "Couldn&#39;t find pictures on that page." in r.text
    assert {v for v, _ in _radios(r.text)} == {"url", "none"}
    assert 'name="image_url"' in r.text and 'value="Lamp"' in r.text


def test_find_no_pictures_same_message(member_client, monkeypatch):
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: [])
    r = member_client.post("/items/new", data={"link": "https://shop.example/x", "action": "find"})
    assert "Couldn&#39;t find pictures on that page." in r.text


def test_find_rate_limited(member_client, monkeypatch):
    called = []
    monkeypatch.setattr(pictures, "allow_fetch", lambda user_id: False)
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: called.append(url) or [])
    r = member_client.post("/items/new", data={"link": "https://shop.example/x", "action": "find"})
    assert r.status_code == 200
    assert "Too many picture searches, try again later." in r.text
    assert called == []
    assert {v for v, _ in _radios(r.text)} == {"url", "none"}


def test_find_without_link_does_not_search(member_client, monkeypatch):
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: pytest.fail("searched"))
    r = member_client.post("/items/new", data={"link": " ", "action": "find"})
    assert r.status_code == 200 and "Paste a shop link first." in r.text
    r = member_client.post("/items/new", data={"link": "javascript:x", "action": "find"})
    assert "must start with http" in r.text
    assert not pictures._fetch_log


def test_find_never_saves(member_client, db, monkeypatch):
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: [_jpeg()])
    member_client.post(
        "/items/new", data={"link": "https://shop.example/x", "name": "Lamp", "action": "find"}
    )
    assert db.query(Item).count() == 0


def test_save_with_candidate(app, member_client, db, monkeypatch):
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: [_jpeg(), _jpeg("blue")])
    html = member_client.post(
        "/items/new", data={"link": "https://shop.example/x", "name": "Lamp", "action": "find"}
    ).text
    second = [v for v, _ in _radios(html) if v.startswith("temp:")][1]
    token = second.split(":", 1)[1]
    candidates = re.findall(r'name="candidate" value="([^"]+)"', html)
    assert len(candidates) == 2  # carried along so a re-render can show them again
    r = member_client.post(
        "/items/new",
        data={"link": "https://shop.example/x", "name": "Lamp", "picture": second,
              "candidate": candidates},
    )
    assert r.status_code == 302
    item = db.query(Item).one()
    assert item.image == token
    with app.app_context():
        assert images.temp_path(token) is None and images.path_for(token)
    assert member_client.get(f"/images/tmp/{token}").status_code == 404


def test_candidates_survive_a_validation_error(member_client, db, monkeypatch):
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: [_jpeg(), _jpeg("blue")])
    html = member_client.post(
        "/items/new", data={"link": "https://shop.example/x", "action": "find"}
    ).text
    temps = [v for v, _ in _radios(html) if v.startswith("temp:")]
    r = member_client.post(
        "/items/new",
        data={"link": "https://shop.example/x", "name": " ", "picture": temps[1],
              "candidate": [t.split(":", 1)[1] for t in temps] + ["../../etc/passwd"]},
    )
    assert r.status_code == 200 and "Name is required." in r.text
    assert [v for v, _ in _radios(r.text) if v.startswith("temp:")] == temps
    assert _checked(r.text) == [temps[1]]
    assert "passwd" not in r.text
    assert db.query(Item).count() == 0


def test_save_with_manual_url(app, member_client, db, local_server, allow_loopback):
    route("/manual.jpg")(lambda h: _send(h, _jpeg(), content_type="image/jpeg"))
    r = member_client.post(
        "/items/new",
        data={"name": "Lamp", "picture": "url", "image_url": local_server + "/manual.jpg"},
    )
    assert r.status_code == 302
    item = db.query(Item).one()
    with app.app_context():
        assert item.image and images.path_for(item.image)


def test_manual_url_required_when_chosen(member_client, db):
    r = member_client.post("/items/new", data={"name": "Lamp", "picture": "url", "image_url": " "})
    assert r.status_code == 200 and "Paste a direct link to a picture." in r.text
    assert db.query(Item).count() == 0


def test_bad_picture_on_new_item_rerenders_as_edit(member_client, db):
    r = member_client.post(
        "/items/new",
        data={"name": "Lamp", "description": "Warm", "picture": "url",
              "image_url": "http://10.0.0.1/x.jpg"},
    )
    assert r.status_code == 200
    assert "Couldn&#39;t use that picture." in r.text
    item = db.query(Item).one()  # the item itself was saved
    assert item.image is None
    assert f'action="/items/{item.id}/edit"' in r.text  # re-submitting does not duplicate it
    assert 'value="Lamp"' in r.text and "Warm" in r.text
    assert re.search(r'name="image_url" value="http://10.0.0.1/x.jpg"', r.text)


def test_unknown_picture_value_changes_nothing(member_client, db):
    assert member_client.post("/items/new", data={"name": "Lamp", "picture": "bogus"}).status_code == 302
    assert db.query(Item).one().image is None


def test_edit_keeps_picture_by_default(app, member_client, db, member):
    item = create_item(db, member, name="Lamp", description=None, link=None)
    with app.test_request_context():
        set_item_image(db, item, ("temp", images.store_temp(_jpeg())))
    name = item.image
    html = member_client.get(f"/items/{item.id}/edit").text
    assert _checked(html) == ["keep"]
    assert f'src="/images/{name}"' in html and "Change" in html
    r = member_client.post(f"/items/{item.id}/edit", data={"name": "Big lamp", "picture": "keep"})
    assert r.status_code == 302
    r = member_client.post(f"/items/{item.id}/edit", data={"name": "Big lamp"})
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Item, item.id).image == name and db.get(Item, item.id).name == "Big lamp"
    member_client.post(f"/items/{item.id}/edit", data={"name": "Big lamp", "picture": "none"})
    db.expire_all()
    assert db.get(Item, item.id).image is None


def test_edit_find_keeps_current_picture_selected(app, member_client, db, member, monkeypatch):
    item = create_item(db, member, name="Lamp", description=None, link=None)
    with app.test_request_context():
        set_item_image(db, item, ("temp", images.store_temp(_jpeg())))
    monkeypatch.setattr(pictures, "find_pictures", lambda url, limit=3: [_jpeg("blue")])
    html = member_client.post(
        f"/items/{item.id}/edit",
        data={"name": "Lamp", "link": "https://shop.example/x", "picture": "keep", "action": "find"},
    ).text
    assert _checked(html) == ["keep"]
    assert len([v for v, _ in _radios(html) if v.startswith("temp:")]) == 1


def test_item_form_has_delete_on_edit(member_client, db, member):
    item = create_item(db, member, name="Lamp", description=None, link=None)
    html = member_client.get(f"/items/{item.id}/edit").text
    assert f'action="/items/{item.id}/delete"' in html
    assert "/delete" not in member_client.get("/items/new").text


# --- list page and theme -------------------------------------------------------------


def test_list_theme_change(member_client, db, member):
    wl = create_wishlist(db, member, name="Birthday", description=None)
    html = member_client.get(f"/lists/{wl.id}").text
    assert 'data-theme="classic"' in html.split(">", 2)[1]  # <html> tag
    assert re.search(r'<input type="radio" name="theme" value="classic" checked>', html)
    r = member_client.post(
        f"/lists/{wl.id}/edit", data={"name": "Birthday", "description": "", "theme": "christmas"}
    )
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Wishlist, wl.id).theme == "christmas"
    html = member_client.get(f"/lists/{wl.id}").text
    assert '<html lang="en" data-theme="christmas"' in html
    # boosted (htmx) navigation swaps only the body, so the body wrapper carries it too
    assert '<div class="page" data-theme="christmas">' in html
    assert re.search(r'<input type="radio" name="theme" value="christmas" checked>', html)

    r = member_client.post(
        f"/lists/{wl.id}/edit", data={"name": "Renamed", "description": "", "theme": "neon"}
    )
    assert r.status_code == 200
    assert "Unknown theme." in r.text and 'role="alert"' in r.text
    assert '<html lang="en" data-theme="christmas"' in r.text
    db.expire_all()
    got = db.get(Wishlist, wl.id)
    assert got.theme == "christmas" and got.name == "Birthday"


def test_list_edit_without_theme_keeps_it(member_client, db, member):
    wl = create_wishlist(db, member, name="Birthday", description=None, theme="birthday")
    assert member_client.post(f"/lists/{wl.id}/edit", data={"name": "B"}).status_code == 302
    db.expire_all()
    assert db.get(Wishlist, wl.id).theme == "birthday"


def test_list_page_items_as_tiles(app, member_client, db, member):
    wl = create_wishlist(db, member, name="Birthday", description=None)
    lamp = create_item(db, member, name="Lamp", description=None, link=None)
    mug = create_item(db, member, name="Mug", description=None, link=None)
    add_items_to_wishlist(db, wl, [lamp.id, mug.id])
    with app.test_request_context():
        set_item_image(db, lamp, ("temp", images.store_temp(_jpeg())))
    html = member_client.get(f"/lists/{wl.id}").text
    tiles = re.findall(r'<a class="tile[^"]*"[^>]*>.*?</a>', html, flags=re.S)
    assert len(tiles) == 2
    assert f'href="/items/{lamp.id}/edit"' in tiles[0]
    assert f'<img src="/images/{lamp.image}" alt=""' in tiles[0]
    assert f'action="/lists/{wl.id}/items/{lamp.id}/remove"' in html


def test_home_cards_and_tiles(member_client, db, member):
    xmas = create_wishlist(db, member, name="Xmas", description=None, theme="christmas")
    bday = create_wishlist(db, member, name="Bday", description=None, theme="birthday")
    lamp = create_item(db, member, name="Lamp", description=None, link=None)
    add_items_to_wishlist(db, xmas, [lamp.id])
    html = member_client.get("/").text
    cards = re.findall(r'<a class="list-card"[^>]*>.*?</a>', html, flags=re.S)
    assert len(cards) == 2
    assert f'href="/lists/{xmas.id}"' in cards[0] and 'data-theme="christmas"' in cards[0]
    assert "1 item" in cards[0] and "Christmas" in cards[0]
    assert 'data-theme="birthday"' in cards[1] and "0 items" in cards[1]
    tiles = re.findall(r'<a class="tile[^"]*"[^>]*>.*?</a>', html, flags=re.S)
    assert len(tiles) == 1 and f'href="/items/{lamp.id}/edit"' in tiles[0]
    assert "New list" in html and "Add gift" in html
    assert 'action="/lists"' in html


def test_home_and_list_hide_reservations(app, member_client, db, member):
    wl = create_wishlist(db, member, name="Birthday", description=None)
    lamp = create_item(db, member, name="Lamp", description=None, link=None)
    add_items_to_wishlist(db, wl, [lamp.id])
    db.add(
        Reservation(
            item_id=lamp.id, reserver_name="Grandma Zosia", guest_token_hash="h",
            via_wishlist_id=wl.id, via_wishlist_name="Birthday",
        )
    )
    db.commit()
    for lang in ("en", "pl"):
        member.language = lang
        db.commit()
        for path in ("/", f"/lists/{wl.id}", f"/items/{lamp.id}/edit"):
            html = member_client.get(path).text
            assert "Lamp" in html, path
            assert "Grandma" not in html and "Zosia" not in html, path
            assert "reserved" not in html.lower() and "zarezerw" not in html.lower(), path
            assert "rezerwacj" not in html.lower() and "reservation" not in html.lower(), path
            assert "is-mine" not in html and "tile-ribbon" not in html and "tile-badge" not in html


# --- cross-cutting rulings -----------------------------------------------------------


def test_htmx_swaps_error_responses(client):
    html = client.get("/").text  # 401 welcome page renders base.html too
    m = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", html)
    assert m
    import json

    handling = json.loads(m.group(1))["responseHandling"]
    assert {"code": "[45]..", "swap": True, "error": True} in handling
    assert handling[0] == {"code": "204", "swap": False}


# --- fix round 1 -------------------------------------------------------------------


def test_manual_url_respects_fetch_limit(member_client, db, member, monkeypatch):
    item = create_item(db, member, name="Lamp", description=None, link=None)
    downloads = []
    monkeypatch.setattr(pictures, "allow_fetch", lambda user_id: False)
    monkeypatch.setattr(pictures, "download_image", lambda url, **kw: downloads.append(url))
    monkeypatch.setattr("wishlist.catalog.download_image", lambda url, **kw: downloads.append(url))
    for path in ("/items/new", f"/items/{item.id}/edit"):
        r = member_client.post(
            path,
            data={"name": "Big lamp", "description": "Warm", "picture": "url",
                  "image_url": "https://shop.example/p.jpg"},
        )
        assert r.status_code == 200
        assert "Too many picture searches, try again later." in r.text
        assert 'value="Big lamp"' in r.text and "Warm" in r.text
        assert 'value="https://shop.example/p.jpg"' in r.text
    assert downloads == []
    db.expire_all()
    assert db.query(Item).count() == 1 and db.get(Item, item.id).name == "Lamp"


def test_invalid_theme_keeps_current_theme_checked(member_client, db, member):
    wl = create_wishlist(db, member, name="Birthday", description=None, theme="birthday")
    r = member_client.post(f"/lists/{wl.id}/edit", data={"name": "Birthday", "theme": "neon"})
    assert r.status_code == 200
    assert re.search(r'<input type="radio" name="theme" value="birthday" checked>', r.text)
    fieldset = re.search(r'<fieldset class="theme-picker"[^>]*>', r.text).group(0)
    assert 'aria-invalid="true"' in fieldset
    error_id = re.search(r'aria-describedby="([^"]+)"', fieldset).group(1)
    assert re.search(r'<p role="alert" id="%s">Unknown theme.</p>' % error_id, r.text)
