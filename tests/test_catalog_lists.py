import pytest
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import NotFound

from wishlist.catalog import (
    add_items_to_wishlist,
    create_item,
    create_wishlist,
    delete_item,
    delete_wishlist,
    get_owned_wishlist,
    remove_item_from_wishlist,
    update_wishlist,
)
from wishlist.errors import ValidationError
from wishlist.models import Item, Reservation, Wishlist


@pytest.fixture
def fresh(db):
    """A new session on the same database: sees only what was committed."""
    s = sessionmaker(bind=db.get_bind())()
    yield s
    s.close()


def _item(db, user, name="Book"):
    return create_item(db, user, name=name, description=None, link=None)


def test_create_wishlist_trims_and_normalizes(db, user):
    wl = create_wishlist(db, user, name="  Birthday ", description="  ")
    assert (wl.name, wl.description, wl.owner_id) == ("Birthday", None, user.id)


@pytest.mark.parametrize(
    "name,description,field",
    [("  ", None, "name"), ("x" * 101, None, "name"), ("ok", "x" * 501, "description")],
)
def test_wishlist_validation(db, user, name, description, field):
    with pytest.raises(ValidationError) as e:
        create_wishlist(db, user, name=name, description=description)
    assert e.value.field == field
    wl = create_wishlist(db, user, name="Ok", description=None)
    with pytest.raises(ValidationError):
        update_wishlist(db, wl, name=name, description=description)


def test_boundary_lengths_accepted(db, user):
    create_wishlist(db, user, name="x" * 100, description="y" * 500)


def test_add_and_remove_items(db, user):
    wl = create_wishlist(db, user, name="L", description=None)
    a, b = _item(db, user, "A"), _item(db, user, "B")
    add_items_to_wishlist(db, wl, [a.id, b.id])
    add_items_to_wishlist(db, wl, [a.id])  # already present: no error, no duplicate
    assert {i.id for i in wl.items} == {a.id, b.id}
    remove_item_from_wishlist(db, wl, a)
    assert [i.id for i in wl.items] == [b.id]
    remove_item_from_wishlist(db, wl, a)  # idempotent


def test_add_foreign_or_missing_or_deleted_item_404(db, user, other_user):
    wl = create_wishlist(db, user, name="L", description=None)
    mine = _item(db, user)
    theirs = _item(db, other_user)
    with pytest.raises(NotFound):
        add_items_to_wishlist(db, wl, [mine.id, theirs.id])
    with pytest.raises(NotFound):
        add_items_to_wishlist(db, wl, [9999])
    assert wl.items == []
    db.add(
        Reservation(item_id=mine.id, reserver_name="Z", guest_token_hash="h", via_wishlist_name="L")
    )
    db.commit()
    db.refresh(mine)
    delete_item(db, mine)  # soft-deleted
    with pytest.raises(NotFound):
        add_items_to_wishlist(db, wl, [mine.id])


def test_delete_wishlist_keeps_items_and_reservations(db, fresh, user):
    wl = create_wishlist(db, user, name="L", description=None)
    item = _item(db, user)
    add_items_to_wishlist(db, wl, [item.id])
    db.add(
        Reservation(
            item_id=item.id, reserver_name="Z", guest_token_hash="h",
            via_wishlist_id=wl.id, via_wishlist_name="L",
        )
    )
    db.commit()
    wl_id, item_id = wl.id, item.id
    delete_wishlist(db, wl)
    assert fresh.get(Wishlist, wl_id) is None
    assert fresh.get(Item, item_id) is not None
    res = fresh.get(Item, item_id).reservation
    assert res is not None and res.via_wishlist_id is None


def test_get_owned_wishlist(db, user, other_user):
    wl = create_wishlist(db, user, name="L", description=None)
    assert get_owned_wishlist(db, user, wl.id) is wl
    with pytest.raises(NotFound):
        get_owned_wishlist(db, other_user, wl.id)
    with pytest.raises(NotFound):
        get_owned_wishlist(db, user, 9999)


def test_mutators_persist(db, fresh, user):
    wl = create_wishlist(db, user, name="L", description="d")
    wl_id = wl.id
    assert fresh.get(Wishlist, wl_id).name == "L"
    update_wishlist(db, wl, name="M", description=None)
    got = fresh.get(Wishlist, wl_id)
    assert (got.name, got.description) == ("M", None)
    item = _item(db, user)
    add_items_to_wishlist(db, wl, [item.id])
    fresh.expire_all()
    assert [i.id for i in fresh.get(Wishlist, wl_id).items] == [item.id]
    remove_item_from_wishlist(db, wl, item)
    fresh.expire_all()
    assert fresh.get(Wishlist, wl_id).items == []
    delete_wishlist(db, wl)
    fresh.expire_all()
    assert fresh.get(Wishlist, wl_id) is None
