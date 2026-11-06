from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from wishlist.db import MIGRATIONS_DIR, Base, upgrade_db
from wishlist.themes import get_settings


def test_upgrade_keeps_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "0001")

    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO user (id, name, email, language, is_admin, key_hash, key_version, created_at) "
            "VALUES (1, 'Ania', 'a@x.pl', 'en', 0, :h, 1, '2026-01-01')"), {"h": "0" * 64})
        c.execute(text(
            "INSERT INTO wishlist (id, owner_id, name, share_version, created_at, updated_at) "
            "VALUES (1, 1, 'L', 1, '2026-01-01', '2026-01-01')"))
        c.execute(text(
            "INSERT INTO item (id, owner_id, name, created_at, updated_at) "
            "VALUES (1, 1, 'Lamp', '2026-01-01', '2026-01-01')"))
        c.execute(text("INSERT INTO wishlist_items (wishlist_id, item_id) VALUES (1, 1)"))
        c.execute(text(
            "INSERT INTO reservation (id, item_id, reserver_name, guest_token_hash, via_wishlist_id, "
            "via_wishlist_name, created_at) VALUES (1, 1, 'Bob', :h, 1, 'L', '2026-01-02')"),
            {"h": "1" * 64})

    upgrade_db(url)

    with engine.connect() as c:
        assert c.execute(text("SELECT name, theme FROM wishlist")).one() == ("L", "classic")
        assert c.execute(text("SELECT name, image FROM item")).one() == ("Lamp", None)
        assert c.execute(text("SELECT reserver_name, item_id, via_wishlist_id FROM reservation")).one() == ("Bob", 1, 1)
        assert c.execute(text("SELECT count(*) FROM wishlist_items")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM user")).scalar() == 1
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []
    with Session(engine) as s:
        st = get_settings(s)
        assert (st.id, st.design, st.default_theme) == (1, "a", "classic")
    engine.dispose()


def test_downgrade_keeps_autoincrement(tmp_path):
    url = f"sqlite:///{tmp_path / 'd.db'}"
    upgrade_db(url)
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["database_url"] = url
    command.downgrade(cfg, "0001")
    engine = create_engine(url)
    with engine.connect() as c:
        for table in ("wishlist", "item"):
            sql = c.execute(text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": table}).scalar()
            assert "AUTOINCREMENT" in sql, table
    engine.dispose()
