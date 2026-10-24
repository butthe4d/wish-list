import pytest
import sqlalchemy as sa

from wishlist.db import upgrade_db
from wishlist.models import Item, Reservation, ShareLog, User, Wishlist


def test_migration_creates_all_tables(app_config):
    upgrade_db(app_config.database_url)
    names = set(sa.inspect(sa.create_engine(app_config.database_url)).get_table_names())
    assert {"user", "item", "wishlist", "wishlist_items", "reservation", "share_log"} <= names


def test_upgrade_db_works_from_other_cwd(app_config, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    upgrade_db(app_config.database_url)
    upgrade_db(app_config.database_url)  # idempotent
    names = set(sa.inspect(sa.create_engine(app_config.database_url)).get_table_names())
    assert "user" in names


def test_sqlite_pragmas(db):
    assert db.scalar(sa.text("PRAGMA foreign_keys")) == 1
    assert db.scalar(sa.text("PRAGMA journal_mode")) == "wal"


def test_user_defaults(db, user):
    db.commit()
    assert user.key_version == 1
    assert user.created_at is not None


def test_one_reservation_per_item(db, user):
    item = Item(owner=user, name="Lamp")
    db.add(item)
    db.flush()
    db.add(Reservation(item=item, reserver_name="A", guest_token_hash="x", via_wishlist_name="L"))
    db.flush()
    db.add(Reservation(item=item, reserver_name="B", guest_token_hash="y", via_wishlist_name="L"))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()


def test_deleting_user_cascades(db, user):
    wl = Wishlist(owner=user, name="B-day")
    item = Item(owner=user, name="Lamp", wishlists=[wl])
    db.add_all([wl, item])
    db.commit()
    db.delete(user)
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(Item)) == 0
    assert db.scalar(sa.select(sa.func.count()).select_from(Wishlist)) == 0


def test_deleting_wishlist_keeps_reservation_name(db, user):
    wl = Wishlist(owner=user, name="B-day")
    item = Item(owner=user, name="Lamp", wishlists=[wl])
    res = Reservation(
        item=item,
        reserver_name="A",
        guest_token_hash="x",
        via_wishlist=wl,
        via_wishlist_name="B-day",
    )
    db.add_all([wl, item, res])
    db.commit()
    db.delete(wl)
    db.commit()
    db.refresh(res)
    assert res.via_wishlist_id is None
    assert res.via_wishlist_name == "B-day"


def test_deleting_item_removes_reservation_and_links(db, user):
    wl = Wishlist(owner=user, name="W")
    item = Item(owner=user, name="Lamp", wishlists=[wl])
    db.add_all([wl, item, Reservation(item=item, reserver_name="A", guest_token_hash="x", via_wishlist_name="W")])
    db.commit()
    db.execute(sa.delete(Item))  # DB-level cascade, bypasses ORM
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(Reservation)) == 0
    assert db.scalar(sa.select(sa.func.count()).select_from(sa.table("wishlist_items"))) == 0


def test_share_log_cascades_with_wishlist(db, user):
    wl = Wishlist(owner=user, name="W")
    wl.share_logs.append(ShareLog(channel="email", recipient="a@b.c"))
    db.add(wl)
    db.commit()
    assert wl.share_version == 1
    db.delete(wl)
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(ShareLog)) == 0


def test_email_unique(db, user):
    db.add(User(name="X", email=user.email, language="pl", is_admin=False, key_hash="1" * 64))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()
