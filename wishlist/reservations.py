from __future__ import annotations

from typing import Literal

from flask_babel import gettext as _
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import ValidationError
from .keys import hash_key
from .models import Item, Reservation, Wishlist

NAME_MAX = 100


class AlreadyReserved(Exception):
    pass


def reserve(
    s: Session, item: Item, via: Wishlist, *, reserver_name: str, guest_token: str
) -> Reservation:
    name = (reserver_name or "").strip()
    if not name:
        raise ValidationError("name", _("Please enter your name."))
    if len(name) > NAME_MAX:
        raise ValidationError("name", _("Name is too long."))
    if item.reservation is not None:
        raise AlreadyReserved()
    res = Reservation(
        item_id=item.id,
        reserver_name=name,
        guest_token_hash=hash_key(guest_token),
        via_wishlist_id=via.id,
        via_wishlist_name=via.name,
    )
    s.add(res)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise AlreadyReserved() from None
    return res


def cancel_by_guest(s: Session, item: Item, guest_token: str) -> bool:
    res = item.reservation
    if res is None or res.guest_token_hash != hash_key(guest_token):
        return False
    s.delete(res)
    s.commit()
    return True


def state_for_guest(
    item: Item, guest_token: str | None
) -> Literal["available", "reserved", "mine"]:
    res = item.reservation
    if res is None:
        return "available"
    if guest_token and res.guest_token_hash == hash_key(guest_token):
        return "mine"
    return "reserved"
