import pytest
from conftest import login, make_user

from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist, delete_item
from wishlist.models import Reservation
from wishlist.reservations import reserve

pytestmark = pytest.mark.usefixtures("catalog_mailer")


def _admin(db):
    from wishlist.models import User

    return db.query(User).filter_by(is_admin=True).one()


def _reserved(db, owner, name="Gift"):
    wl = create_wishlist(db, owner, name="Birthday", description=None)
    item = create_item(db, owner, name=name, description=None, link=None)
    add_items_to_wishlist(db, wl, [item.id])
    reserve(db, item, wl, reserver_name="Mom", guest_token="t")
    db.refresh(item)
    return item, wl


@pytest.fixture
def member(db):
    return make_user(db, "Ola", "ola@example.com")[0]


def _assert_hidden(r, *secrets):
    text = r.get_data(as_text=True)
    for secret in secrets:
        assert secret not in text
    assert "reserved by" not in text.lower() and "cancel reservation" not in text.lower()
    return text


def _assert_revealed(r):
    text = r.get_data(as_text=True)
    assert 'hx-history="false"' in text
    assert r.headers["Cache-Control"] == "no-store"
    return text


@pytest.mark.parametrize("whose", ["member", "admin"])
def test_member_page_hidden_until_reveal(admin_client, db, member, whose):
    owner = member if whose == "member" else _admin(db)
    _reserved(db, owner)
    r = admin_client.get(f"/admin/members/{owner.id}")
    text = _assert_hidden(r, "Mom")
    assert "Birthday" in text  # lists and items are still shown
    assert f"/admin/members/{owner.id}?reveal=1" in text
    assert "no-store" not in r.headers.get("Cache-Control", "")
    text = _assert_revealed(admin_client.get(f"/admin/members/{owner.id}?reveal=1"))
    assert "Mom" in text and "Cancel reservation" in text


def test_reveal_is_per_member(admin_client, db, member):
    _reserved(db, member, "TheirGift")
    other = make_user(db, "Pat", "pat@example.com")[0]
    _reserved(db, other, "PatGift")
    text = admin_client.get(f"/admin/members/{member.id}?reveal=1").get_data(as_text=True)
    assert "Mom" in text and "PatGift" not in text


def test_index_shows_only_neutral_marker(admin_client, db, member):
    admin = _admin(db)
    mine, _ = _reserved(db, admin, "MyGift")
    theirs, _ = _reserved(db, member, "TheirGift")
    delete_item(db, mine)
    delete_item(db, theirs)
    r = admin_client.get("/admin/")
    text = _assert_hidden(r, "Mom", "MyGift", "TheirGift", "Birthday")
    assert "Show reservations" not in text
    assert text.count("Needs attention") == 1  # the member's row, not the admin's own
    row = text[text.index(f"/admin/members/{member.id}"):]
    assert row.index("Needs attention") < row.index("</tr>")


def test_index_has_no_marker_without_orphans(admin_client, db, member):
    _reserved(db, member)
    assert "Needs attention" not in admin_client.get("/admin/").get_data(as_text=True)


def test_member_page_warnings_only_when_revealed(admin_client, db, member):
    item, _ = _reserved(db, member, "TheirGift")
    delete_item(db, item)
    text = _assert_hidden(admin_client.get(f"/admin/members/{member.id}"), "Mom", "TheirGift")
    assert "needs your attention" in text
    text = _assert_revealed(admin_client.get(f"/admin/members/{member.id}?reveal=1"))
    assert "TheirGift" in text and "Mom" in text and "Dismiss warning" in text


def test_index_links_member_rows(admin_client, member):
    assert f"/admin/members/{member.id}" in admin_client.get("/admin/").get_data(as_text=True)


@pytest.mark.parametrize("whose", ["member", "admin"])
def test_cancel_and_dismiss_need_reveal(admin_client, db, member, whose):
    owner = member if whose == "member" else _admin(db)
    item, _ = _reserved(db, owner)
    delete_item(db, item)
    rid = db.query(Reservation).one().id
    for action in ("cancel", "dismiss"):
        assert admin_client.post(
            f"/admin/reservations/{rid}/{action}", data={"back_uid": owner.id}
        ).status_code == 404
    db.expire_all()
    assert db.query(Reservation).one().warning_dismissed_at is None
    r = admin_client.post(
        f"/admin/reservations/{rid}/dismiss", data={"reveal": "1", "back_uid": owner.id}
    )
    assert r.headers["Location"] == f"/admin/members/{owner.id}?reveal=1"
    db.expire_all()
    assert db.query(Reservation).one().warning_dismissed_at is not None
    r = admin_client.post(
        f"/admin/reservations/{rid}/cancel", data={"reveal": "1", "back_uid": owner.id}
    )
    assert r.status_code == 302 and db.query(Reservation).count() == 0


def test_non_admin_gets_404(client, db, member):
    item, _ = _reserved(db, member)
    _, key = make_user(db, "Pat", "pat@example.com")
    login(client, key)
    assert client.get(f"/admin/members/{member.id}").status_code == 404
    assert client.post(f"/admin/reservations/{item.reservation.id}/cancel").status_code == 404
    assert client.post(f"/admin/reservations/{item.reservation.id}/dismiss").status_code == 404


def test_member_view_of_reserved_removed_item_reveals_nothing(client, db):
    owner, key = make_user(db, "Own", "own@example.com")
    item, wl = _reserved(db, owner, "OwnGift")
    delete_item(db, item)
    login(client, key)
    for path in ("/", f"/lists/{wl.id}"):
        r = client.get(path)
        text = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "Mom" not in text and "OwnGift" not in text and "reserved" not in text.lower()
