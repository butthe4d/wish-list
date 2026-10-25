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


def test_admin_sees_member_reserver(admin_client, db, member):
    _reserved(db, member)
    r = admin_client.get(f"/admin/members/{member.id}")
    text = r.get_data(as_text=True)
    assert "Mom" in text and "Birthday" in text and "Cancel reservation" in text


def test_admin_own_lists_hidden_until_reveal(admin_client, db):
    admin = _admin(db)
    _reserved(db, admin)
    r = admin_client.get(f"/admin/members/{admin.id}")
    text = r.get_data(as_text=True)
    assert "Mom" not in text and "reserved" not in text.lower()
    assert f"/admin/members/{admin.id}?reveal=1" in text
    assert "no-store" not in r.headers.get("Cache-Control", "")
    r = admin_client.get(f"/admin/members/{admin.id}?reveal=1")
    text = r.get_data(as_text=True)
    assert "Mom" in text
    assert 'hx-history="false"' in text
    assert r.headers["Cache-Control"] == "no-store"


def test_admin_index_hides_own_warnings(admin_client, db, member):
    admin = _admin(db)
    mine, _ = _reserved(db, admin, "MyGift")
    theirs, _ = _reserved(db, member, "TheirGift")
    delete_item(db, mine)
    delete_item(db, theirs)
    text = admin_client.get("/admin/").get_data(as_text=True)
    assert "TheirGift" in text and "Mom" in text and "MyGift" not in text
    r = admin_client.get("/admin/?reveal=1")
    assert "MyGift" in r.get_data(as_text=True)
    assert 'hx-history="false"' in r.get_data(as_text=True)
    assert r.headers["Cache-Control"] == "no-store"


def test_index_links_member_rows(admin_client, member):
    assert f"/admin/members/{member.id}" in admin_client.get("/admin/").get_data(as_text=True)


def test_cancel_others_reservation_without_reveal(admin_client, db, member):
    item, _ = _reserved(db, member)
    r = admin_client.post(
        f"/admin/reservations/{item.reservation.id}/cancel", data={"back_uid": member.id}
    )
    assert r.status_code == 302 and r.headers["Location"] == f"/admin/members/{member.id}"
    assert db.query(Reservation).count() == 0


def test_own_cancel_and_dismiss_need_reveal(admin_client, db):
    admin = _admin(db)
    item, _ = _reserved(db, admin)
    delete_item(db, item)
    rid = db.query(Reservation).one().id
    for action in ("cancel", "dismiss"):
        assert admin_client.post(f"/admin/reservations/{rid}/{action}").status_code == 404
    db.expire_all()
    assert db.query(Reservation).one().warning_dismissed_at is None
    r = admin_client.post(f"/admin/reservations/{rid}/dismiss", data={"reveal": "1"})
    assert r.headers["Location"] == "/admin/?reveal=1"
    db.expire_all()
    assert db.query(Reservation).one().warning_dismissed_at is not None
    r = admin_client.post(f"/admin/reservations/{rid}/cancel", data={"reveal": "1"})
    assert r.status_code == 302 and db.query(Reservation).count() == 0


def test_dismiss_other_redirects_to_index(admin_client, db, member):
    item, _ = _reserved(db, member)
    delete_item(db, item)
    rid = db.query(Reservation).one().id
    r = admin_client.post(f"/admin/reservations/{rid}/dismiss")
    assert r.headers["Location"] == "/admin/"


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
