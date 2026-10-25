from __future__ import annotations

import logging
from typing import Literal

from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import ValidationError
from .keys import hash_key
from .messages import orphan_email
from .models import Item, Reservation, User, Wishlist, utcnow
from .notify import DeliveryError, Mailer

log = logging.getLogger(__name__)

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


def is_orphaned(r: Reservation) -> bool:
    return r.item.deleted_at is not None or not r.item.wishlists


def orphaned_reservations(s: Session, viewer: User, *, reveal_own: bool) -> list[Reservation]:
    pending = s.scalars(
        select(Reservation).where(Reservation.warning_dismissed_at.is_(None)).order_by(Reservation.id)
    )
    return [
        r
        for r in pending
        if is_orphaned(r) and (reveal_own or r.item.owner_id != viewer.id)
    ]


def notify_orphan(s: Session, item: Item, mailer: Mailer | None) -> None:
    """Email every admin except the item's owner. Delivery errors are logged, never raised."""
    r = item.reservation
    if mailer is None or r is None or not is_orphaned(r):
        return
    admins = s.scalars(select(User).where(User.is_admin.is_(True), User.id != item.owner_id))
    for admin in list(admins):
        try:
            subject, body = orphan_email(admin, r)
            mailer.send(admin.email, subject, body)
        except DeliveryError as e:
            log.warning("orphan notification to admin %s failed: %s", admin.id, e)


def cancel_by_admin(s: Session, r: Reservation) -> None:
    item = r.item
    s.delete(r)
    if item.deleted_at is not None:
        s.delete(item)
    s.commit()


def dismiss_warning(s: Session, r: Reservation) -> None:
    r.warning_dismissed_at = utcnow()
    s.commit()
