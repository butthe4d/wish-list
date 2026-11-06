import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import render_template, render_template_string

from conftest import login, make_user
from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist
from wishlist.themes import THEMES, get_settings, update_settings

STATIC = Path(__file__).resolve().parent.parent / "wishlist" / "static"

pytestmark = pytest.mark.usefixtures("catalog_mailer")


def test_default_theme_on_member_pages(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    update_settings(db, design="a", default_theme="christmas")
    login(client, key)
    html = client.get("/").text
    assert 'data-theme="christmas"' in html and 'data-design="a"' in html
    assert "design-a.css" in html and "themes.css" in html and "pico" not in html


def test_default_theme_on_anonymous_pages(client, db):
    update_settings(db, design="a", default_theme="birthday")
    for path, status in [("/", 401), ("/nope", 404), ("/u/bad-key", 404)]:
        r = client.get(path)
        assert r.status_code == status
        assert 'data-theme="birthday"' in r.text


def test_view_can_override_theme(app, db):
    update_settings(db, design="a", default_theme="classic")
    with app.test_request_context("/"):
        html = render_template("not_found.html", theme="christmas")
    assert 'data-theme="christmas"' in html


def test_pico_removed():
    assert not (STATIC / "pico.classless.min.css").exists()


def test_fonts_self_hosted(admin_client, db):
    for path in ["/", "/items/new", "/admin/"]:
        assert "fonts.googleapis.com" not in admin_client.get(path).text
    css = admin_client.get("/static/design-a.css").text
    assert "fonts.googleapis" not in css
    refs = re.findall(r"url\((?:\"|')?/static/fonts/([^)\"']+)", css)
    assert len(refs) == 8
    for name in refs:
        assert (STATIC / "fonts" / name).is_file()
        assert admin_client.get(f"/static/fonts/{name}").status_code == 200
    assert (STATIC / "fonts" / "OFL.txt").is_file()


def test_every_theme_defines_all_variables():
    css = (STATIC / "themes.css").read_text()
    for key in THEMES:
        block = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\n\}' % key, css, re.S)
        assert block, key
        for var in ["--accent", "--accent-ink", "--accent-text", "--tile-1", "--tile-2",
                    "--tile-3", "--tile-4", "--ribbon", "--page-bg", "--hero-decoration"]:
            assert re.search(r"%s\s*:" % re.escape(var), block.group(1)), (key, var)


def _luminance(hex_color):
    def channel(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(hex_color[i:i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_text_contrast():
    css = (STATIC / "themes.css").read_text()
    for key in THEMES:
        block = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\n\}' % key, css, re.S).group(1)
        var = dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;", block))
        assert _contrast(var["--accent-text"], "#FFFFFF") >= 4.5, key
        assert _contrast(var["--accent-text"], var["--page-bg"]) >= 4.5, key
        assert _contrast(var["--accent-ink"], var["--accent"]) >= 4.5, key


def test_admin_settings_shown(admin_client, db):
    html = admin_client.get("/admin/").text
    assert 'action="/admin/settings"' in html
    assert 'name="design"' in html and 'name="default_theme"' in html
    assert 'value="classic" selected' in html


def test_admin_settings_update(admin_client, db):
    r = admin_client.post("/admin/settings", data={"design": "a", "default_theme": "birthday"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")
    db.expire_all()
    assert get_settings(db).default_theme == "birthday"
    assert "Saved." in admin_client.get("/admin/").text


def test_admin_settings_invalid(admin_client, db):
    r = admin_client.post("/admin/settings", data={"design": "a", "default_theme": "neon"})
    assert r.status_code == 200
    assert "Unknown theme." in r.text and 'role="alert"' in r.text
    db.expire_all()
    assert get_settings(db).default_theme == "classic"


def test_admin_settings_non_admin_404(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    login(client, key)
    r = client.post("/admin/settings", data={"design": "a", "default_theme": "birthday"})
    assert r.status_code == 404
    db.expire_all()
    assert get_settings(db).default_theme == "classic"


def test_every_page_renders(app, admin_client, db):
    from wishlist.models import User

    admin = db.query(User).filter_by(email="maciej@example.com").one()
    wl = create_wishlist(db, admin, name="Gwiazdka", description=None)
    item = create_item(db, admin, name="Lamp", description=None, link=None)
    add_items_to_wishlist(db, wl, [item.id])
    pages = {
        "/": 200, "/items/new": 200, f"/items/{item.id}/edit": 200, f"/lists/{wl.id}": 200,
        "/admin/": 200, f"/admin/members/{admin.id}": 200, "/admin/members/new": 200,
        f"/admin/members/{admin.id}/edit": 200, "/nope": 404,
    }
    for path, status in pages.items():
        r = admin_client.get(path)
        assert r.status_code == status, path
        assert "Lista życzeń" in r.text or "Wishlist" in r.text, path
        assert 'name="lang"' in r.text, path
    assert app.test_client().get("/").status_code == 401


def test_header_label_translated(client, db):
    _, key = make_user(db, "Ania", "a@x.pl", language="pl")
    login(client, key)
    html = client.get("/").text
    assert "Lista życzeń" in html and 'lang="pl"' in html


def _macro(app, call, **ctx):
    with app.test_request_context("/"):
        return render_template_string('{% import "_macros.html" as m %}' + call, **ctx)


def test_tile_macro_states(app):
    item = SimpleNamespace(name="Lamp", id=3)
    plain = _macro(app, '{{ m.tile(item, "/x", index=5) }}', item=item)
    assert 'href="/x"' in plain and "Lamp" in plain and "var(--tile-2)" in plain
    assert "ribbon" not in plain and "badge" not in plain
    reserved = _macro(app, '{{ m.tile(item, "/x", state="reserved") }}', item=item)
    assert "Reserved<" in reserved and "Someone is already getting this" in reserved
    mine = _macro(app, '{{ m.tile(item, "/x", state="mine") }}', item=item)
    assert "badge" in mine and "Your reservation" in mine and "Reserved<" not in mine
    pic = _macro(app, '{{ m.tile(item, "/x", image_url="/images/a.jpg") }}', item=item)
    assert 'src="/images/a.jpg"' in pic and "var(--tile-" not in pic


def test_icon_and_theme_picker_macros(app):
    for name in ["gift", "tree", "cake", "check", "lock", "external", "close", "back", "copy", "plus"]:
        assert "<svg" in _macro(app, "{{ m.icon(%r) }}" % name)
    picker = _macro(app, '{{ m.theme_picker("christmas") }}')
    assert picker.count('type="radio"') == 3 and picker.count('name="theme"') == 3
    assert re.search(r'value="christmas"\s+checked', picker)
    assert "Classic" in picker and "Birthday" in picker
    named = _macro(app, '{{ m.theme_picker("classic", name="default_theme") }}')
    assert 'name="default_theme"' in named
