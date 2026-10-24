from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask, current_app
from flask.globals import app_ctx
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Base(DeclarativeBase):
    pass


def _scope() -> int:
    return id(app_ctx._get_current_object())


def init_db(app: Flask) -> None:
    url = app.config["WISHLIST"].database_url
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.extensions["wishlist_engine"] = engine
    app.extensions["wishlist_session"] = scoped_session(factory, scopefunc=_scope)

    @app.teardown_appcontext
    def _remove_session(_exc):
        app.extensions["wishlist_session"].remove()


def get_session() -> Session:
    return current_app.extensions["wishlist_session"]()


def upgrade_db(database_url: str) -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")
