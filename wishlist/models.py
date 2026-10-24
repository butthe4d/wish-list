from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


wishlist_items = Table(
    "wishlist_items",
    Base.metadata,
    Column("wishlist_id", ForeignKey("wishlist.id", ondelete="CASCADE"), primary_key=True),
    Column("item_id", ForeignKey("item.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "user"
    # never reuse ids: sessions and share links reference them
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    language: Mapped[str] = mapped_column(String(5))
    is_admin: Mapped[bool] = mapped_column(default=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list[Item]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    wishlists: Mapped[list[Wishlist]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000))
    link: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    owner: Mapped[User] = relationship(back_populates="items")
    wishlists: Mapped[list[Wishlist]] = relationship(
        secondary=wishlist_items, back_populates="items"
    )
    reservation: Mapped[Reservation | None] = relationship(
        back_populates="item", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )


class Wishlist(Base):
    __tablename__ = "wishlist"
    # never reuse ids: sessions and share links reference them
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    share_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    owner: Mapped[User] = relationship(back_populates="wishlists")
    items: Mapped[list[Item]] = relationship(secondary=wishlist_items, back_populates="wishlists")
    share_logs: Mapped[list[ShareLog]] = relationship(
        back_populates="wishlist", cascade="all, delete-orphan", passive_deletes=True
    )


class Reservation(Base):
    __tablename__ = "reservation"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id", ondelete="CASCADE"), unique=True)
    reserver_name: Mapped[str] = mapped_column(String(100))
    guest_token_hash: Mapped[str] = mapped_column(String(64))
    via_wishlist_id: Mapped[int | None] = mapped_column(
        ForeignKey("wishlist.id", ondelete="SET NULL")
    )
    via_wishlist_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    warning_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)

    item: Mapped[Item] = relationship(back_populates="reservation")
    via_wishlist: Mapped[Wishlist | None] = relationship()


class ShareLog(Base):
    __tablename__ = "share_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    wishlist_id: Mapped[int] = mapped_column(ForeignKey("wishlist.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(10))
    recipient: Mapped[str] = mapped_column(String(254))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    wishlist: Mapped[Wishlist] = relationship(back_populates="share_logs")
