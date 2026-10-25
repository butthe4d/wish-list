import logging

import pytest
from conftest import make_user

from fakes import FailingMailer
from wishlist.catalog import (
    add_items_to_wishlist,
    create_item,
    create_wishlist,
    delete_item,
    delete_wishlist,
    remove_item_from_wishlist,
    update_item,
)
from wishlist.models import Item, Reservation
from wishlist.reservations import (
    cancel_by_admin,
    dismiss_warning,
    notify_orphan,
    orphaned_reservations,
    reserve,
)

pytestmark = pytest.mark.usefixtures("catalog_mailer")


@pytest.fixture
def member(db):
    return make_user(db, "Ola", "ola@example.com")[0]


@pytest.fixture
def admin(db):
    return make_user(db, "Maciej", "maciej@example.com", is_admin=True, language="pl")[0]


def _reserved(db, owner, *, lists=1):
    wls = [create_wishlist(db, owner, name=f"L{i}", description=None) for i in range(lists)]
    item = create_item(db, owner, name="Lamp", description=None, link=None)
    for wl in wls:
        add_items_to_wishlist(db, wl, [item.id])
    reserve(db, item, wls[0], reserver_name="Mom", guest_token="t")
    return item, wls


def test_soft_deleted_reserved_item_warns_and_emails_admin(db, member, admin, mailer):
    item, _ = _reserved(db, member)
    delete_item(db, item)
    assert [r.reserver_name for r in orphaned_reservations(db, admin, reveal_own=False)] == ["Mom"]
    assert len(mailer.sent) == 1
    assert mailer.sent[0][0] == admin.email
    assert "Mom" in mailer.sent[0][2]


def test_removed_from_last_list_warns(db, member, admin, mailer):
    item, (wl,) = _reserved(db, member)
    remove_item_from_wishlist(db, wl, item)
    assert len(orphaned_reservations(db, admin, reveal_own=False)) == 1
    assert len(mailer.sent) == 1


def test_update_removing_from_all_lists_warns(db, member, admin, mailer):
    item, _ = _reserved(db, member)
    update_item(db, item, name="Lamp", description=None, link=None, wishlist_ids=[])
    assert len(mailer.sent) == 1


def test_delete_wishlist_warns_for_each_orphaned_item(db, member, admin, mailer):
    item, (wl,) = _reserved(db, member)
    delete_wishlist(db, wl)
    assert len(orphaned_reservations(db, admin, reveal_own=False)) == 1
    assert len(mailer.sent) == 1


def test_still_in_other_list_no_warning(db, member, admin, mailer):
    item, (wl1, wl2) = _reserved(db, member, lists=2)
    remove_item_from_wishlist(db, wl1, item)
    assert orphaned_reservations(db, admin, reveal_own=False) == []
    assert mailer.sent == []


def test_admin_own_item_hidden_and_no_email(db, admin, mailer):
    item, _ = _reserved(db, admin)
    delete_item(db, item)
    assert orphaned_reservations(db, admin, reveal_own=False) == []
    assert len(orphaned_reservations(db, admin, reveal_own=True)) == 1
    assert mailer.sent == []


def test_other_admins_emailed_in_their_language_owner_admin_skipped(db, member, admin, mailer):
    other, _ = make_user(db, "Zed", "zed@example.com", is_admin=True, language="en")
    item, _ = _reserved(db, other)
    delete_item(db, item)
    assert [m[0] for m in mailer.sent] == [admin.email]


def test_delivery_error_is_logged_not_raised(db, member, admin, caplog):
    item, _ = _reserved(db, member)
    delete_item(db, item)
    caplog.set_level(logging.WARNING)
    notify_orphan(db, item, FailingMailer())
    assert "notification" in caplog.text
    assert "Mom" not in caplog.text


def test_cancel_by_admin_purges_soft_deleted(db, member, admin):
    item, _ = _reserved(db, member)
    delete_item(db, item)
    iid = item.id
    cancel_by_admin(db, db.query(Reservation).one())
    assert db.get(Item, iid) is None
    assert db.query(Reservation).count() == 0


def test_cancel_by_admin_keeps_live_item(db, member):
    item, _ = _reserved(db, member)
    cancel_by_admin(db, item.reservation)
    assert db.get(Item, item.id) is not None
    assert db.query(Reservation).count() == 0


def test_dismiss_hides_warning(db, member, admin):
    item, _ = _reserved(db, member)
    delete_item(db, item)
    dismiss_warning(db, orphaned_reservations(db, admin, reveal_own=False)[0])
    assert orphaned_reservations(db, admin, reveal_own=False) == []
