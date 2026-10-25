import pytest

from wishlist.catalog import add_items_to_wishlist, create_item, create_wishlist
from wishlist.errors import ValidationError
from wishlist.models import Reservation
from wishlist.reservations import AlreadyReserved, cancel_by_guest, reserve, state_for_guest


def test_reserve_stores_hash_and_list_name(db, item_in_list):
    item, wl = item_in_list
    res = reserve(db, item, wl, reserver_name="  Mom ", guest_token="g1")
    db.expire_all()
    r = db.query(Reservation).one()
    assert r.reserver_name == "Mom" and r.via_wishlist_name == "Birthday"
    assert r.guest_token_hash != "g1" and r.id == res.id


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_name(db, item_in_list, name):
    item, wl = item_in_list
    with pytest.raises(ValidationError) as e:
        reserve(db, item, wl, reserver_name=name, guest_token="g1")
    assert e.value.field == "name"
    assert db.query(Reservation).count() == 0


def test_second_reservation_rejected(db, item_in_list):
    item, wl = item_in_list
    reserve(db, item, wl, reserver_name="Mom", guest_token="g1")
    db.refresh(item)
    with pytest.raises(AlreadyReserved):
        reserve(db, item, wl, reserver_name="Dad", guest_token="g2")
    assert item.reservation.reserver_name == "Mom"


def test_integrity_error_race_becomes_already_reserved(db, item_in_list):
    item, wl = item_in_list
    reserve(db, item, wl, reserver_name="Mom", guest_token="g1")
    db.refresh(item)
    db.expire(item)
    # a stale view of the item that does not see the reservation yet
    from sqlalchemy.orm import sessionmaker

    other = sessionmaker(bind=db.get_bind())()
    stale = other.get(type(item), item.id)
    stale.__dict__["reservation"] = None
    with pytest.raises(AlreadyReserved):
        reserve(other, stale, wl, reserver_name="Dad", guest_token="g2")
    other.close()
    assert db.query(Reservation).one().reserver_name == "Mom"


def test_state_for_guest(db, item_in_list):
    item, wl = item_in_list
    assert state_for_guest(item, "g1") == "available"
    reserve(db, item, wl, reserver_name="Mom", guest_token="g1")
    db.refresh(item)
    assert state_for_guest(item, "g1") == "mine"
    assert state_for_guest(item, "g2") == "reserved"
    assert state_for_guest(item, None) == "reserved"


def test_cancel_only_by_same_guest(db, item_in_list):
    item, wl = item_in_list
    reserve(db, item, wl, reserver_name="Mom", guest_token="g1")
    db.refresh(item)
    assert cancel_by_guest(db, item, "g2") is False
    assert db.query(Reservation).count() == 1
    assert cancel_by_guest(db, item, "g1") is True
    assert db.query(Reservation).count() == 0
    db.refresh(item)
    assert cancel_by_guest(db, item, "g1") is False


def test_reservation_visible_in_every_list(db, user):
    a = create_wishlist(db, user, name="A", description=None)
    b = create_wishlist(db, user, name="B", description=None)
    item = create_item(db, user, name="Lamp", description=None, link=None)
    add_items_to_wishlist(db, a, [item.id])
    add_items_to_wishlist(db, b, [item.id])
    reserve(db, item, a, reserver_name="Mom", guest_token="g1")
    db.refresh(item)
    assert state_for_guest(item, "g2") == "reserved"
    assert state_for_guest(b.items[0], "g2") == "reserved"
    assert state_for_guest(b.items[0], "g1") == "mine"
