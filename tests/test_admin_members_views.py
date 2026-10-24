from conftest import login, make_user
from fakes import FailingMailer

from wishlist import create_app
from wishlist.models import User
from wishlist.notify import DISABLED


def _create(c, **over):
    data = {"name": "Ola", "email": "Ola@x.pl", "language": "pl"}
    data.update(over)
    return c.post("/admin/members/new", data=data)


def _link(html):
    start = html.index("https://wish.example/u/")
    return html[start:].split('"')[0]


def test_admin_creates_member_and_link_emailed(admin_client, mailer, db):
    r = _create(admin_client)
    assert r.status_code == 200 and "/u/" in r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert 'readonly' in r.text
    to, subject, body = mailer.sent[0]
    assert to == "ola@x.pl" and _link(r.text) in body
    assert db.query(User).filter_by(email="ola@x.pl").one().language == "pl"


def test_link_shown_when_email_fails(app_config, db):
    app = create_app(app_config, mailer=FailingMailer())
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    _, key = make_user(db, "Maciej", "m@x.pl", is_admin=True)
    c = app.test_client()
    login(c, key)
    r = _create(c)
    assert r.status_code == 200 and "Couldn't send" in r.text and "/u/" in r.text


def test_link_shown_when_no_mailer(app_config, db):
    app = create_app(app_config, mailer=DISABLED)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    _, key = make_user(db, "Maciej", "m@x.pl", is_admin=True)
    c = app.test_client()
    login(c, key)
    r = _create(c)
    assert "Email isn't configured" in r.text and "/u/" in r.text


def test_link_not_in_session_cookie(admin_client):
    r = _create(admin_client)
    key = _link(r.text).rsplit("/u/", 1)[1]
    assert all(key not in h for h in r.headers.getlist("Set-Cookie"))
    cookie = admin_client.get_cookie("session")
    assert key not in cookie.value
    from flask.sessions import SecureCookieSessionInterface
    app = admin_client.application
    data = SecureCookieSessionInterface().get_signing_serializer(app).loads(cookie.value)
    assert key not in str(dict(data))
    assert key not in admin_client.get("/admin/").text


def test_validation_error_rerenders_form(admin_client, db):
    r = _create(admin_client, email="bad")
    assert r.status_code == 200 and 'role="alert"' in r.text and "Ola" in r.text
    assert db.query(User).count() == 1


def test_rotate_key_invalidates_old_link(admin_client, client, db, mailer):
    u, old = make_user(db, "Ola", "ola@x.pl")
    login(client, old)
    assert client.get("/").status_code == 200
    r = admin_client.post(f"/admin/members/{u.id}/rotate-key")
    assert r.status_code == 200 and mailer.sent[-1][0] == "ola@x.pl"
    assert client.get("/").status_code == 401
    assert client.get(f"/u/{old}").status_code == 404
    new = _link(r.text).rsplit("/u/", 1)[1]
    assert client.get(f"/u/{new}").status_code == 302


def test_non_admin_gets_404_and_no_nav(client, db):
    _, key = make_user(db, "Ola", "ola@x.pl")
    login(client, key)
    for path in ("/admin/", "/admin/members/new"):
        assert client.get(path).status_code == 404
    assert "/admin/" not in client.get("/").text


def test_admin_nav_link_and_index(admin_client, db):
    make_user(db, "Ola", "ola@x.pl")
    assert 'href="/admin/"' in admin_client.get("/").text
    html = admin_client.get("/admin/").text
    assert "ola@x.pl" in html and "Maciej" in html and 'id="warnings"' in html


def test_edit_member(admin_client, db):
    u, _ = make_user(db, "Ola", "ola@x.pl")
    assert "ola@x.pl" in admin_client.get(f"/admin/members/{u.id}/edit").text
    r = admin_client.post(f"/admin/members/{u.id}/edit",
                          data={"name": "Olka", "email": "o@x.pl", "language": "en"})
    assert r.status_code == 302
    db.refresh(u)
    assert (u.name, u.email) == ("Olka", "o@x.pl")


def test_edit_error_leaves_member_unchanged(admin_client, db):
    u, _ = make_user(db, "Ola", "ola@x.pl")
    make_user(db, "B", "b@x.pl")
    r = admin_client.post(f"/admin/members/{u.id}/edit",
                          data={"name": "Olka", "email": "b@x.pl", "language": "en"})
    assert r.status_code == 200 and 'role="alert"' in r.text
    db.refresh(u)
    assert u.name == "Ola"


def test_delete_requires_confirmation_and_blocks_self(admin_client, db):
    u, _ = make_user(db, "Ola", "ola@x.pl")
    admin = db.query(User).filter_by(is_admin=True).one()
    admin_client.post(f"/admin/members/{u.id}/delete")
    assert db.query(User).count() == 2
    r = admin_client.post(f"/admin/members/{admin.id}/delete", data={"confirm": "yes"},
                          follow_redirects=True)
    assert "can&#39;t delete yourself" in r.text and db.query(User).count() == 2
    admin_client.post(f"/admin/members/{u.id}/delete", data={"confirm": "yes"})
    db.expire_all()
    assert db.query(User).count() == 1


def test_unknown_member_404(admin_client):
    assert admin_client.get("/admin/members/999/edit").status_code == 404
    assert admin_client.post("/admin/members/999/rotate-key").status_code == 404
