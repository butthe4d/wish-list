import io

import pytest
from PIL import Image

from conftest import ROUTES, route, _send
from wishlist.pictures import PageInfo, PictureError, extract_details, find_pictures, inspect_page


@pytest.fixture
def shop_routes():
    before = dict(ROUTES)
    yield
    ROUTES.clear()
    ROUTES.update(before)


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_title_order_and_suffix():
    html = '''<meta property="og:site_name" content="Sklep XYZ">
      <meta property="og:title" content="Słuchawki X200 | Sklep XYZ">
      <title>ignored</title>'''
    assert extract_details(html, "https://sklep.example/p/1")[0] == "Słuchawki X200"


def test_title_falls_back_to_jsonld_then_title_tag():
    ld = '<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Kubek LD"}]}</script>'
    assert extract_details(ld + "<title>nope</title>", "https://s.example/")[0] == "Kubek LD"
    html = "<title>Kubek &amp; spodek - x-kom</title>"
    assert extract_details(html, "https://www.x-kom.pl/p/1")[0] == "Kubek & spodek"
    tw = '<meta name="twitter:title" content="Tw"><title>T</title>'
    assert extract_details(tw, "https://s.example/")[0] == "Tw"


def test_title_suffix_kept_when_unrelated():
    html = "<title>Zestaw - 3 sztuki</title>"
    assert extract_details(html, "https://kubki.example/")[0] == "Zestaw - 3 sztuki"


def test_title_limit():
    assert len(extract_details(f"<title>{'a' * 500}</title>", "https://s.example/")[0]) == 200


def test_description_cleanup_and_limit():
    html = '<meta property="og:description" content="Fajny &lt;b&gt;kubek&lt;/b&gt;  &amp;\n spodek">'
    assert extract_details(html, "https://s.example/")[1] == "Fajny kubek & spodek"
    long = " ".join(["słowo"] * 400)
    desc = extract_details(f'<meta name="description" content="{long}">', "https://s.example/")[1]
    assert len(desc) <= 301 and desc.endswith("…") and not desc[:-1].endswith(" ")
    assert desc[:-1].split(" ")[-1] == "słowo"


def test_description_sources_and_jsonld_html():
    ld = '<script type="application/ld+json">{"@type":"Product","description":"<p>Opis <b>LD</b></p>"}</script>'
    assert extract_details(ld + '<meta name="description" content="m">', "https://s.example/")[1] == "Opis LD"
    assert extract_details('<meta name="twitter:description" content="tw">', "https://s.example/")[1] == "tw"


def test_title_only_shop_name_is_none():
    html = '<meta property="og:site_name" content="Sklep XYZ"><meta property="og:title" content="Sklep XYZ">'
    assert extract_details(html, "https://sklep.example/")[0] is None


def test_garbage_html_returns_none():
    assert extract_details("<<<&&", "https://x.example/") == (None, None)


def test_inspect_page_details_without_pictures(app, local_server, allow_loopback, shop_routes):
    page = b'<meta property="og:title" content="Kubek">'
    route("/shop")(lambda h: _send(h, page, content_type="text/html"))
    with app.app_context():
        assert inspect_page(local_server + "/shop") == PageInfo("Kubek", None, [])


def test_inspect_page_returns_pictures(app, local_server, allow_loopback, shop_routes):
    page = b'<meta property="og:title" content="Kubek"><img src="/a.png">'
    route("/shop")(lambda h: _send(h, page, content_type="text/html"))
    route("/a.png")(lambda h: _send(h, _png(), content_type="image/png"))
    with app.app_context():
        info = inspect_page(local_server + "/shop")
    assert info.title == "Kubek" and len(info.pictures) == 1


def test_find_pictures_still_raises_without_pictures(app, local_server, allow_loopback, shop_routes):
    page = b'<meta property="og:title" content="Kubek">'
    route("/shop")(lambda h: _send(h, page, content_type="text/html"))
    with app.app_context(), pytest.raises(PictureError):
        find_pictures(local_server + "/shop")
