import pytest
from conftest import make_user

from wishlist import members
from wishlist.errors import ValidationError
from wishlist.keys import hash_key
from wishlist.models import User


def _new(db, name="Ola", email="ola@x.pl", language="en", is_admin=False):
    return members.create_member(db, name=name, email=email, language=language, is_admin=is_admin)


def test_create_member_returns_key_matching_hash(db):
    u, key = _new(db, email=" Ola@X.pl ")
    assert u.email == "ola@x.pl" and u.key_hash == hash_key(key)
    assert db.query(User).count() == 1


def test_create_member_duplicate_email(db):
    _new(db, email="A@x.pl")
    with pytest.raises(ValidationError) as e:
        _new(db, name="B", email="a@x.pl")
    assert e.value.field == "email"


@pytest.mark.parametrize(
    "kw,field",
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"name": "Bob\nBcc: evil"}, "name"),
        ({"email": "nope"}, "email"),
        ({"email": "a@" + "b" * 250 + ".pl"}, "email"),
        ({"language": "de"}, "language"),
    ],
)
def test_create_member_validation(db, kw, field):
    with pytest.raises(ValidationError) as e:
        _new(db, **kw)
    assert e.value.field == field


def test_cannot_demote_last_admin(db):
    admin, _ = make_user(db, "Maciej", "m@x.pl", is_admin=True)
    with pytest.raises(ValidationError) as e:
        members.update_member(db, admin, name="Maciej", email="m@x.pl", language="en",
                              is_admin=False)
    assert e.value.field == "is_admin"
    other, _ = make_user(db, "Z", "z@x.pl", is_admin=True)
    members.update_member(db, other, name="Z", email="z@x.pl", language="pl",
                          is_admin=False)
    assert (other.is_admin, other.language) == (False, "pl")


def test_update_member_email_conflict(db):
    a, _ = make_user(db, "A", "a@x.pl", is_admin=True)
    make_user(db, "B", "b@x.pl")
    with pytest.raises(ValidationError) as e:
        members.update_member(db, a, name="A", email="B@x.pl", language="en",
                              is_admin=True)
    assert e.value.field == "email"


def test_cannot_delete_self(db):
    admin, _ = make_user(db, "Maciej", "m@x.pl", is_admin=True)
    with pytest.raises(ValidationError):
        members.delete_member(db, admin, admin)
    assert db.query(User).count() == 1


def test_delete_member_cascades(db, item_in_list, user):
    admin, _ = make_user(db, "Maciej", "m@x.pl", is_admin=True)
    members.delete_member(db, user, admin)
    from wishlist.models import Item, Wishlist
    assert db.query(Item).count() == 0 and db.query(Wishlist).count() == 0


def test_rotate_key_changes_hash_and_version(db):
    u, key = _new(db)
    new = members.rotate_key(db, u)
    assert new != key and u.key_hash == hash_key(new) and u.key_version == 2
