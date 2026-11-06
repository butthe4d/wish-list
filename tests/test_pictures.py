import io

import pytest
from PIL import Image

import wishlist.pictures as pictures
from conftest import ROUTES, route, _send
from wishlist.pictures import PictureError, extract_candidates, find_pictures, process_image


def _png(size=(300, 200), mode="RGB", color=(200, 30, 30), **save):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG", **save)
    return buf.getvalue()


def test_extract_order_and_resolution():
    html = '''<meta property="og:image" content="/img/a.jpg">
      <meta name="twitter:image" content="https://cdn.shop/b.jpg">
      <script type="application/ld+json">{"@type":"Product","image":["https://cdn.shop/c.jpg"]}</script>
      <img src="/sprite.png"><img src="logo.svg"><img src="/d.jpg" width="40" height="40"><img src="/e.jpg">'''
    assert extract_candidates(html, "https://shop.example/p/1") == [
        "https://shop.example/img/a.jpg", "https://cdn.shop/b.jpg", "https://cdn.shop/c.jpg",
        "https://shop.example/e.jpg"]


def test_extract_handles_garbage():
    assert extract_candidates("<html><<>>&&", "https://x.example/") == []


def test_extract_json_ld_variants_and_other_sources():
    html = '''<meta property="og:image:secure_url" content="https://s/1.jpg">
      <script type="application/ld+json">{"@graph":[{"@type":"Product","image":{"url":"https://s/2.jpg"}},
        {"@type":"Product","image":"https://s/3.jpg"},{"@type":"Person","image":"https://s/nope.jpg"}]}</script>
      <script type="application/ld+json">{broken</script>
      <link rel="image_src" href="https://s/4.jpg">
      <img data-src="/5.jpg"><img srcset="/6.jpg 1x, /7.jpg 2x"><img src="data:image/png;base64,AAA">
      <img src="/1.jpg"><img src="/my-icon.png"><img src="/p.jpg" width="99">'''
    assert extract_candidates(html, "https://s/") == [f"https://s/{n}.jpg" for n in (1, 2, 3, 4, 5, 6)]


def test_extract_limits_to_12_and_http_only():
    html = "".join(f'<img src="/i{n}.jpg">' for n in range(20)) + '<meta property="og:image" content="ftp://x/y.jpg">'
    out = extract_candidates(html, "https://s/")
    assert len(out) == 12 and all(u.startswith("https://") for u in out)


def test_process_image_resizes_and_strips():
    img = Image.new("RGBA", (3000, 1500), (255, 0, 0, 0))
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    buf = io.BytesIO()
    img.save(buf, "PNG", exif=exif)
    out = Image.open(io.BytesIO(process_image(buf.getvalue())))
    assert out.format == "JPEG" and out.mode == "RGB"
    assert max(out.size) == 800 and out.size == (800, 400)
    assert not out.getexif()
    assert out.getpixel((10, 10))[0] > 240 and out.getpixel((10, 10))[1] > 240  # transparent -> white


def test_process_image_applies_exif_orientation():
    img = Image.new("RGB", (200, 100), "blue")
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    assert Image.open(io.BytesIO(process_image(buf.getvalue()))).size == (100, 200)


def test_process_image_rejects_non_image_and_bomb(monkeypatch):
    with pytest.raises(PictureError):
        process_image(b"hello")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(PictureError):
        process_image(_png((100, 100)))


def test_max_pixels_is_set():
    assert pictures.Image.MAX_IMAGE_PIXELS == 40_000_000


@pytest.fixture
def shop_routes():
    before = dict(ROUTES)
    yield
    ROUTES.clear()
    ROUTES.update(before)


def test_find_pictures_returns_three_valid(app, local_server, allow_loopback, shop_routes):
    page = (
        '<meta property="og:image" content="/missing.jpg">'
        '<meta name="twitter:image" content="/corrupt.jpg">'
        + "".join(f'<img src="/ok{n}.png">' for n in range(1, 5))
    )
    route("/shop")(lambda h: _send(h, page.encode(), content_type="text/html; charset=utf-8"))
    route("/corrupt.jpg")(lambda h: _send(h, b"not an image", content_type="image/jpeg"))
    for n in range(1, 5):
        route(f"/ok{n}.png")(lambda h: _send(h, _png(), content_type="image/png"))
    with app.app_context():
        result = find_pictures(local_server + "/shop")
    assert len(result) == 3
    assert all(Image.open(io.BytesIO(r)).format == "JPEG" for r in result)


def test_find_pictures_tries_at_most_eight(app, local_server, allow_loopback, shop_routes):
    page = "".join(f'<img src="/bad{n}.jpg">' for n in range(12))
    route("/shop")(lambda h: _send(h, page.encode(), content_type="text/html"))
    hits = []
    for n in range(12):
        route(f"/bad{n}.jpg")(lambda h: (hits.append(h.path), _send(h, b"x", content_type="image/jpeg")))
    with app.app_context():
        assert find_pictures(local_server + "/shop") == []
    assert len(hits) == 8


def test_decode_html_uses_declared_charset():
    text = "<img src='/zażółć.png'>"
    raw = text.encode("iso-8859-2")
    assert pictures._decode_html(raw, "text/html; charset=iso-8859-2") == text
    assert pictures._decode_html(b'<meta charset="iso-8859-2">' + raw, "text/html").endswith(text)
    assert pictures._decode_html(raw, "text/html") != text  # no declaration: utf-8 with replacement
    assert pictures._decode_html(raw, "text/html; charset=nonsense")  # unknown charset falls back


def test_find_pictures_follows_charset_decoded_url(app, local_server, allow_loopback, shop_routes):
    page = '<meta charset="iso-8859-2"><img src="/zażółć.png">'.encode("iso-8859-2")
    route("/shop")(lambda h: _send(h, page, content_type="text/html"))
    route("/za%C5%BC%C3%B3%C5%82%C4%87.png")(lambda h: _send(h, _png(), content_type="image/png"))
    with app.app_context():
        assert len(find_pictures(local_server + "/shop")) == 1


def test_find_pictures_budget(app, local_server, allow_loopback, shop_routes, monkeypatch):
    import time

    monkeypatch.setattr(pictures, "FIND_BUDGET", 1)
    page = "".join(f'<img src="/slow{n}.png">' for n in range(5))
    route("/shop")(lambda h: _send(h, page.encode(), content_type="text/html"))
    hits = []

    def slow(h):
        hits.append(h.path)
        time.sleep(1.5)
        _send(h, _png(), content_type="image/png")

    for n in range(5):
        route(f"/slow{n}.png")(slow)
    started = time.monotonic()
    with app.app_context(), pytest.raises(PictureError):
        find_pictures(local_server + "/shop")
    assert time.monotonic() - started < 2.5
    assert len(hits) == 1


def test_find_pictures_budget_keeps_collected(app, local_server, allow_loopback, shop_routes, monkeypatch):
    import time

    monkeypatch.setattr(pictures, "FIND_BUDGET", 1)
    page = '<img src="/fast.png"><img src="/slow.png"><img src="/never.png">'
    route("/shop")(lambda h: _send(h, page.encode(), content_type="text/html"))
    route("/fast.png")(lambda h: _send(h, _png(), content_type="image/png"))
    route("/slow.png")(lambda h: (time.sleep(1.5), _send(h, _png(), content_type="image/png")))
    route("/never.png")(lambda h: _send(h, _png(), content_type="image/png"))
    with app.app_context():
        assert len(find_pictures(local_server + "/shop")) == 1


def test_extract_survives_deeply_nested_json_ld():
    nested = "[" * 100000 + "]" * 100000
    html = f'<script type="application/ld+json">{nested}</script><img src="/after.jpg">'
    assert extract_candidates(html, "https://s/") == ["https://s/after.jpg"]


def test_skip_words_apply_to_file_name_only():
    html = '<img src="https://iconic-store.com/silicon-cable.jpg"><img src="/img/icon-cart.png"><img src="/img/sprite.png">'
    assert extract_candidates(html, "https://s/") == ["https://iconic-store.com/silicon-cable.jpg"]


def test_srcset_with_commas_in_url():
    html = '<img srcset="/w_200,h_200/x.jpg 1x, /w_400,h_400/x.jpg 2x">'
    assert extract_candidates(html, "https://s/") == ["https://s/w_200,h_200/x.jpg"]


def test_percentage_and_non_pixel_sizes_not_treated_as_small():
    html = '<img src="/a.jpg" width="50%"><img src="/b.jpg" width="80px"><img src="/c.jpg" height="auto">'
    assert extract_candidates(html, "https://s/") == ["https://s/a.jpg", "https://s/c.jpg"]


def test_process_jpeg_large_uses_draft():
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), "green").save(buf, "JPEG")
    assert max(Image.open(io.BytesIO(process_image(buf.getvalue()))).size) == 800


def test_find_pictures_page_not_html(app, local_server, allow_loopback, shop_routes):
    route("/shop")(lambda h: _send(h, b"{}", content_type="application/json"))
    with app.app_context(), pytest.raises(PictureError):
        find_pictures(local_server + "/shop")


def test_find_pictures_page_fetch_error(app, local_server, allow_loopback):
    with app.app_context(), pytest.raises(PictureError):
        find_pictures(local_server + "/nothing-here")


def test_operator_deny_list_is_passed(app, local_server, allow_loopback, shop_routes):
    route("/shop")(lambda h: _send(h, b"<html>", content_type="text/html"))
    app.config["WISHLIST"] = app.config["WISHLIST"].__class__(
        **{**app.config["WISHLIST"].__dict__, "fetch_deny_cidrs": ("127.0.0.0/8",)}
    )
    with app.app_context(), pytest.raises(PictureError):
        find_pictures(local_server + "/shop")
