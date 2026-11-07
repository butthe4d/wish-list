"""Item form: name and description from the shop page ("Find pictures"), and the
suggestions shown before replacing text the member typed."""

import io
import re

import pytest
from PIL import Image

import wishlist.pictures as pictures
from conftest import login, make_user
from wishlist.catalog import create_item
from wishlist.models import Item
from wishlist.pictures import PageInfo

pytestmark = pytest.mark.usefixtures("catalog_mailer")

SHOP = "https://shop.example/x"


def _jpeg(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(buf, "JPEG")
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
def member_client(app, member_and_key):
    c = app.test_client()
    login(c, member_and_key[1])
    return c


def _page(monkeypatch, title="Słuchawki X200", description="Czarne", pics=None):
    calls = []

    def fake(url, limit=3):
        calls.append(url)
        return PageInfo(title, description, [_jpeg()] if pics is None else pics)

    monkeypatch.setattr(pictures, "inspect_page", fake)
    return calls


def _name_value(html):
    return re.search(r'<input type="text" name="name" value="([^"]*)"', html).group(1)


def _description_value(html):
    return re.search(r'<textarea name="description"[^>]*>([^<]*)</textarea>', html).group(1)


def _candidates(html):
    return re.findall(r'name="candidate" value="([^"]+)"', html)


def _temp_radios(html):
    return re.findall(r'name="picture" value="temp:([^"]+)"', html)


def test_find_fills_empty_fields(member_client, monkeypatch):
    _page(monkeypatch)
    r = member_client.post("/items/new", data={"link": SHOP, "action": "find"})
    assert r.status_code == 200
    assert _name_value(r.text) == "Słuchawki X200"
    assert _description_value(r.text) == "Czarne"
    assert "shop-suggestion" not in r.text
    assert len(_temp_radios(r.text)) == 1
    assert len(pictures._fetch_log[next(iter(pictures._fetch_log))]) == 1  # one search counted


def test_find_suggests_for_differing_fields(member_client, monkeypatch):
    _page(monkeypatch)
    r = member_client.post(
        "/items/new", data={"link": SHOP, "name": "Słuchawki", "description": "Moje", "action": "find"}
    )
    html = r.text
    assert _name_value(html) == "Słuchawki"  # never replaced silently
    assert _description_value(html) == "Moje"
    assert '<div class="shop-suggestion" data-field="name"' in html
    assert '<div class="shop-suggestion" data-field="description"' in html
    assert "From the shop: «Słuchawki X200»" in html
    assert "From the shop: «Czarne»" in html
    assert '<input type="hidden" name="suggest_name" value="Słuchawki X200">' in html
    assert '<input type="hidden" name="suggest_description" value="Czarne">' in html
    assert re.search(r'<button type="submit"[^>]*name="action" value="apply:name"[^>]*>Replace</button>', html)
    assert re.search(r'<button type="submit"[^>]*name="action" value="apply:description"', html)
    # the suggestion sits below its own field
    assert html.index('name="name"') < html.index('data-field="name"') < html.index('name="description"')
    assert html.index('<textarea name="description"') < html.index('data-field="description"')
    assert "Use the shop's text?" in html and "Keep mine" in html  # popup strings for suggest.js


def test_identical_value_no_suggestion(member_client, monkeypatch):
    _page(monkeypatch)
    r = member_client.post(
        "/items/new",
        data={"link": SHOP, "name": "  Słuchawki   X200 ", "description": "Czarne\n", "action": "find"},
    )
    assert "shop-suggestion" not in r.text
    assert 'name="suggest_' not in r.text


def test_suggestion_values_are_escaped(member_client, monkeypatch):
    _page(monkeypatch, title='<img src=x onerror="alert(1)">', description=None)
    r = member_client.post("/items/new", data={"link": SHOP, "name": "Mine", "action": "find"})
    assert "<img src=x" not in r.text
    assert "&lt;img src=x onerror=" in r.text


def test_apply_button_replaces_and_keeps_candidates(member_client, db, monkeypatch):
    _page(monkeypatch, pics=[_jpeg(), _jpeg("blue")])
    html = member_client.post(
        "/items/new", data={"link": SHOP, "name": "Słuchawki", "description": "Moje", "action": "find"}
    ).text
    tokens = _candidates(html)
    assert len(tokens) == 2
    searches = sum(len(v) for v in pictures._fetch_log.values())
    monkeypatch.setattr(pictures, "inspect_page", lambda url, limit=3: pytest.fail("fetched"))
    r = member_client.post(
        "/items/new",
        data={"link": SHOP, "name": "Słuchawki", "description": "Moje", "picture": f"temp:{tokens[1]}",
              "candidate": tokens, "suggest_name": "Słuchawki X200", "suggest_description": "Czarne",
              "action": "apply:name"},
    )
    assert r.status_code == 200
    assert _name_value(r.text) == "Słuchawki X200"
    assert _description_value(r.text) == "Moje"
    assert 'data-field="name"' not in r.text and 'name="suggest_name"' not in r.text
    assert 'data-field="description"' in r.text  # the other suggestion stays
    assert _temp_radios(r.text) == tokens and _candidates(r.text) == tokens
    assert f'value="temp:{tokens[1]}" checked' in r.text
    assert db.query(Item).count() == 0  # nothing saved
    assert sum(len(v) for v in pictures._fetch_log.values()) == searches  # not a search


def test_apply_ignores_unknown_field_and_forged_token(member_client, db, monkeypatch):
    monkeypatch.setattr(pictures, "inspect_page", lambda url, limit=3: pytest.fail("fetched"))
    r = member_client.post(
        "/items/new",
        data={"link": SHOP, "name": "Lamp", "suggest_link": "https://evil.example/",
              "candidate": ["../x", "../../etc/passwd"], "action": "apply:link"},
    )
    assert r.status_code == 200
    assert _name_value(r.text) == "Lamp"
    assert 'value="https://shop.example/x"' in r.text and "evil.example" not in r.text
    assert 'name="candidate"' not in r.text and "passwd" not in r.text
    assert db.query(Item).count() == 0


def test_details_without_pictures_still_fill(member_client, monkeypatch):
    _page(monkeypatch, title="Słuchawki X200", description=None, pics=[])
    r = member_client.post("/items/new", data={"link": SHOP, "action": "find"})
    assert _name_value(r.text) == "Słuchawki X200"
    assert _description_value(r.text) == ""
    assert "Couldn&#39;t find pictures on that page." in r.text
    assert not _temp_radios(r.text)


def test_page_error_fills_nothing(member_client, monkeypatch):
    def boom(url, limit=3):
        raise pictures.PictureError("blocked")

    monkeypatch.setattr(pictures, "inspect_page", boom)
    r = member_client.post("/items/new", data={"link": SHOP, "action": "find"})
    assert _name_value(r.text) == ""
    assert "Couldn&#39;t find pictures on that page." in r.text


def test_edit_form_never_replaces_silently(member_client, db, member_and_key, monkeypatch):
    item = create_item(db, member_and_key[0], name="Lamp", description="Warm", link=None)
    _page(monkeypatch, title="Lampa LED", description="Ciepła")
    r = member_client.post(
        f"/items/{item.id}/edit",
        data={"name": "Lamp", "description": "Warm", "link": SHOP, "action": "find"},
    )
    assert _name_value(r.text) == "Lamp"
    assert "From the shop: «Lampa LED»" in r.text
    r = member_client.post(
        f"/items/{item.id}/edit",
        data={"name": "Lamp", "description": "Warm", "link": SHOP, "suggest_name": "Lampa LED",
              "action": "apply:name"},
    )
    assert _name_value(r.text) == "Lampa LED"
    db.expire_all()
    assert db.get(Item, item.id).name == "Lamp"  # stored name unchanged until save
    r = member_client.post(
        f"/items/{item.id}/edit", data={"name": "Lampa LED", "description": "Warm", "link": SHOP}
    )
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Item, item.id).name == "Lampa LED"


def test_suggest_js_loaded_only_on_item_form(member_client, db, member_and_key):
    item = create_item(db, member_and_key[0], name="Lamp", description=None, link=None)
    assert "suggest.js" in member_client.get("/items/new").text
    assert "suggest.js" in member_client.get(f"/items/{item.id}/edit").text
    assert "suggest.js" not in member_client.get("/").text


def test_polish_strings(app, db, monkeypatch):
    _, key = make_user(db, "Ola", "ola@x.pl", language="pl")
    c = app.test_client()
    login(c, key)
    _page(monkeypatch)
    html = c.post("/items/new", data={"link": SHOP, "name": "Moje", "action": "find"}).text
    assert "Ze sklepu: «Słuchawki X200»" in html
    assert ">Zamień</button>" in html
    assert "Użyć tekstu ze sklepu?" in html and "Zostaw moje" in html
