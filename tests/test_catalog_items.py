import pytest
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import NotFound

from wishlist.catalog import (
    create_item,
    delete_item,
    get_owned_item,
    normalize_link,
    set_item_wishlists,
    update_item,
)
from wishlist.errors import ValidationError
from wishlist.models import Item, Reservation, Wishlist


def _reserve(db, item):
    db.add(
        Reservation(
            item_id=item.id, reserver_name="Zosia", guest_token_hash="h", via_wishlist_name="L"
        )
    )
    db.commit()
    db.refresh(item)


def _item(db, user, **kw):
    return create_item(db, user, name="Book", description=None, link=None, **kw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        (None, None),
        ("  ", None),
        ("example.com/x", "https://example.com/x"),
        ("localhost:8080/x", "https://localhost:8080/x"),
        ("http://shop.pl/a?b=1", "http://shop.pl/a?b=1"),
    ],
)
def test_normalize_link(raw, expected):
    assert normalize_link(raw) == expected


@pytest.mark.parametrize(
    "raw", ["javascript:alert(1)", "ftp://x.pl/a", "https://", "mailto:a@b.pl", "https:///x"]
)
def test_normalize_link_rejects(raw):
    with pytest.raises(ValidationError) as e:
        normalize_link(raw)
    assert e.value.field == "link"


def test_create_item_validates_and_strips(app, db, user):
    item = create_item(db, user, name="  Book ", description="  ", link="shop.pl")
    assert (item.name, item.description, item.link) == ("Book", None, "https://shop.pl")
    for kw, field in [
        ({"name": "  "}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"name": "ok", "description": "x" * 2001}, "description"),
    ]:
        args = {"name": "ok", "description": None, "link": None} | kw
        with pytest.raises(ValidationError) as e, app.test_request_context():
            create_item(db, user, **args)
        assert e.value.field == field


def test_create_item_in_lists(db, user):
    w = Wishlist(owner_id=user.id, name="Bday")
    db.add(w)
    db.commit()
    item = _item(db, user, wishlist_ids=[w.id])
    assert item.wishlists == [w]


def test_delete_unreserved_item_is_hard(db, user):
    item = _item(db, user)
    item_id = item.id
    delete_item(db, item)
    assert db.get(Item, item_id) is None


def test_delete_reserved_item_is_soft(db, user):
    w = Wishlist(owner_id=user.id, name="Bday")
    db.add(w)
    db.commit()
    item = _item(db, user, wishlist_ids=[w.id])
    _reserve(db, item)
    delete_item(db, item)
    db.refresh(item)
    assert item.deleted_at is not None
    assert item.wishlists == []
    assert item.reservation is not None


def test_cannot_put_item_in_foreign_list(db, user, other_user):
    foreign = Wishlist(owner_id=other_user.id, name="Theirs")
    db.add(foreign)
    db.commit()
    item = _item(db, user)
    with pytest.raises(NotFound):
        set_item_wishlists(db, item, [foreign.id])
    with pytest.raises(NotFound):
        set_item_wishlists(db, item, [9999])


def test_update_replaces_lists_and_keeps_reservation(db, user):
    a, b = Wishlist(owner_id=user.id, name="A"), Wishlist(owner_id=user.id, name="B")
    db.add_all([a, b])
    db.commit()
    item = _item(db, user, wishlist_ids=[a.id])
    _reserve(db, item)
    update_item(db, item, name="Novel", description="d", link="", wishlist_ids=[b.id])
    db.refresh(item)
    assert item.name == "Novel"
    assert item.wishlists == [b]
    assert item.reservation is not None


def test_get_owned_item(db, user, other_user):
    item = _item(db, user)
    assert get_owned_item(db, user, item.id) is item
    with pytest.raises(NotFound):
        get_owned_item(db, other_user, item.id)
    with pytest.raises(NotFound):
        get_owned_item(db, user, 9999)
    _reserve(db, item)
    delete_item(db, item)
    with pytest.raises(NotFound):
        get_owned_item(db, user, item.id)


@pytest.fixture
def fresh(db):
    """A new session on the same database: sees only what was committed."""
    s = sessionmaker(bind=db.get_bind())()
    yield s
    s.close()


def test_mutators_persist(db, fresh, user):
    user_id = user.id
    db.commit()
    item = create_item(db, user, name="Book", description=None, link=None)
    assert fresh.get(Item, item.id).name == "Book"
    update_item(db, item, name="Novel", description=None, link=None)
    assert fresh.get(Item, item.id).name == "Novel"
    item_id = item.id
    delete_item(db, item)
    assert fresh.get(Item, item_id) is None

    kept = create_item(db, user, name="Kept", description=None, link=None)
    _reserve(db, kept)
    delete_item(db, kept)
    assert fresh.get(Item, kept.id).deleted_at is not None
    assert user_id == kept.owner_id
