from conftest import login, make_user
from wishlist.keys import hash_key, new_key


def test_link_logs_in_and_redirects(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    r = client.get(f"/u/{key}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert client.get("/").status_code == 200


def test_unknown_key_404(client):
    assert client.get("/u/nope").status_code == 404


def test_anonymous_gets_401_welcome(client):
    assert client.get("/").status_code == 401


def test_rotated_key_kills_session(client, db):
    u, key = make_user(db, "Ania", "a@x.pl")
    login(client, key)
    u.key_version += 1
    u.key_hash = hash_key(new_key())
    db.commit()
    assert client.get("/").status_code == 401
    assert client.get(f"/u/{key}").status_code == 404


def test_non_admin_gets_404_on_admin(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    login(client, key)
    assert client.get("/admin/").status_code == 404


def test_admin_gets_admin_page(client, db):
    _, key = make_user(db, "Boss", "b@x.pl", is_admin=True)
    login(client, key)
    assert client.get("/admin/").status_code == 200


def test_session_cookie_settings(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    r = client.get(f"/u/{key}")
    cookie = r.headers["Set-Cookie"]
    assert "Secure" in cookie and "SameSite=Lax" in cookie and "Expires=" in cookie
