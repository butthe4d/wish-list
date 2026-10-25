from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from flask import abort
from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ValidationError
from .models import Item, User, Wishlist, utcnow
from .notify import get_mailer
from .reservations import notify_orphan

NAME_MAX = 200
WISHLIST_NAME_MAX = 100
WISHLIST_DESCRIPTION_MAX = 500
DESCRIPTION_MAX = 2000
LINK_MAX = 2000
# "scheme:" that is not "host:port"
_OPAQUE_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:(?!\d+(/|$))")


def normalize_link(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        if _OPAQUE_SCHEME.match(raw):  # "javascript:alert(1)", "mailto:x", ...
            raise ValidationError("link", _("The link must start with http:// or https://."))
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValidationError("link", _("The link must start with http:// or https://."))
    try:
        host = parts.hostname
    except ValueError:
        host = None
    if not host:
        raise ValidationError("link", _("The link is not a valid address."))
    if len(raw) > LINK_MAX:
        raise ValidationError("link", _("The link is too long."))
    return raw


def _clean_fields(name: str, description: str | None, link: str | None):
    name = (name or "").strip()
    if not name:
        raise ValidationError("name", _("Name is required."))
    if len(name) > NAME_MAX:
        raise ValidationError("name", _("Name is too long."))
    description = (description or "").strip() or None
    if description is not None and len(description) > DESCRIPTION_MAX:
        raise ValidationError("description", _("Description is too long."))
    return name, description, normalize_link(link)


def set_item_wishlists(s: Session, item: Item, wishlist_ids: Iterable[int]) -> None:
    ids = set(wishlist_ids)
    lists = []
    if ids:
        lists = list(
            s.scalars(
                select(Wishlist).where(Wishlist.id.in_(ids), Wishlist.owner_id == item.owner_id)
            )
        )
        if len(lists) != len(ids):
            abort(404)
    item.wishlists = lists


def create_item(
    s: Session,
    owner: User,
    *,
    name: str,
    description: str | None,
    link: str | None,
    wishlist_ids: Iterable[int] = (),
) -> Item:
    name, description, link = _clean_fields(name, description, link)
    item = Item(owner_id=owner.id, name=name, description=description, link=link)
    s.add(item)
    s.flush()
    try:
        set_item_wishlists(s, item, wishlist_ids)
    except Exception:
        s.rollback()
        raise
    s.commit()
    return item


def update_item(
    s: Session,
    item: Item,
    *,
    name: str,
    description: str | None,
    link: str | None,
    wishlist_ids: Iterable[int] = (),
) -> Item:
    name, description, link = _clean_fields(name, description, link)
    set_item_wishlists(s, item, wishlist_ids)
    item.name, item.description, item.link = name, description, link
    item.updated_at = utcnow()
    s.commit()
    notify_orphan(s, item, get_mailer())
    return item


def delete_item(s: Session, item: Item) -> None:
    if item.reservation is not None:
        item.deleted_at = utcnow()
        item.wishlists = []
        s.commit()
        notify_orphan(s, item, get_mailer())
    else:
        s.delete(item)
        s.commit()


def get_owned_item(s: Session, user: User, item_id: int) -> Item:
    item = s.get(Item, item_id)
    if item is None or item.owner_id != user.id or item.deleted_at is not None:
        abort(404)
    return item


def _clean_wishlist_fields(name: str, description: str | None):
    name = (name or "").strip()
    if not name:
        raise ValidationError("name", _("Name is required."))
    if len(name) > WISHLIST_NAME_MAX:
        raise ValidationError("name", _("Name is too long."))
    description = (description or "").strip() or None
    if description is not None and len(description) > WISHLIST_DESCRIPTION_MAX:
        raise ValidationError("description", _("Description is too long."))
    return name, description


def create_wishlist(s: Session, owner: User, *, name: str, description: str | None) -> Wishlist:
    name, description = _clean_wishlist_fields(name, description)
    wl = Wishlist(owner_id=owner.id, name=name, description=description)
    s.add(wl)
    s.commit()
    return wl


def update_wishlist(s: Session, wl: Wishlist, *, name: str, description: str | None) -> Wishlist:
    name, description = _clean_wishlist_fields(name, description)
    wl.name, wl.description = name, description
    wl.updated_at = utcnow()
    s.commit()
    return wl


def delete_wishlist(s: Session, wl: Wishlist) -> None:
    items = list(wl.items)
    s.delete(wl)
    s.commit()
    for item in items:
        notify_orphan(s, item, get_mailer())


def add_items_to_wishlist(s: Session, wl: Wishlist, item_ids: Iterable[int]) -> None:
    ids = set(item_ids)
    if not ids:
        return
    items = list(
        s.scalars(
            select(Item).where(
                Item.id.in_(ids), Item.owner_id == wl.owner_id, Item.deleted_at.is_(None)
            )
        )
    )
    if len(items) != len(ids):
        abort(404)
    present = {i.id for i in wl.items}
    wl.items.extend(i for i in items if i.id not in present)
    s.commit()


def remove_item_from_wishlist(s: Session, wl: Wishlist, item: Item) -> None:
    if item in wl.items:
        wl.items.remove(item)
    s.commit()
    notify_orphan(s, item, get_mailer())


def get_owned_wishlist(s: Session, user: User, wishlist_id: int) -> Wishlist:
    wl = s.get(Wishlist, wishlist_id)
    if wl is None or wl.owner_id != user.id:
        abort(404)
    return wl
