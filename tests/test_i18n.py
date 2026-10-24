from conftest import login, make_user


def test_guest_gets_accept_language(client):
    r = client.get("/", headers={"Accept-Language": "pl-PL,pl;q=0.9"})
    assert 'lang="pl"' in r.text


def test_guest_default_language(client):
    assert 'lang="en"' in client.get("/").text


def test_guest_page_is_translated(client):
    r = client.get("/", headers={"Accept-Language": "pl"})
    assert "Witaj" in r.text


def test_cookie_beats_accept_language(client):
    client.post("/lang", data={"lang": "en"})
    r = client.get("/", headers={"Accept-Language": "pl"})
    assert 'lang="en"' in r.text


def test_switcher_sets_cookie_and_member_pref(client, db):
    u, key = make_user(db, "Ania", "a@x.pl", language="en")
    login(client, key)
    r = client.post("/lang", data={"lang": "pl", "next": "/"})
    assert r.status_code == 302
    db.refresh(u)
    assert u.language == "pl"
    assert 'lang="pl"' in client.get("/").text


def test_member_language_beats_cookie(client, db):
    _, key = make_user(db, "Ola", "o@x.pl", language="pl")
    login(client, key)
    client.set_cookie("lang", "en")
    assert 'lang="pl"' in client.get("/").text


def test_switcher_rejects_unknown_and_external_next(client):
    assert client.post("/lang", data={"lang": "de"}).status_code == 400
    r = client.post("/lang", data={"lang": "pl", "next": "https://evil.example/"})
    assert r.headers["Location"] == "/"


def test_switcher_rejects_protocol_relative_next(client):
    for bad in ("//evil.example", "/\\evil.example", "evil", ""):
        r = client.post("/lang", data={"lang": "pl", "next": bad})
        assert r.headers["Location"] == "/"


def test_switcher_keeps_relative_next(client):
    r = client.post("/lang", data={"lang": "pl", "next": "/admin/?x=1"})
    assert r.headers["Location"] == "/admin/?x=1"


def test_base_layout_has_switcher_and_csrf(client):
    html = client.get("/").text
    assert "English" in html and "Polski" in html
    assert 'name="csrf_token"' in html
    assert 'hx-boost="true"' in html
