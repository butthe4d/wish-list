from __future__ import annotations

import logging
from typing import Literal

from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import images
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


def _pending_orphans(s: Session) -> list[Reservation]:
    pending = s.scalars(
        select(Reservation).where(Reservation.warning_dismissed_at.is_(None)).order_by(Reservation.id)
    )
    return [r for r in pending if is_orphaned(r)]


def orphaned_reservations(s: Session, owner: User) -> list[Reservation]:
    """Undismissed warnings on one member's items. Only for a page the admin has
    explicitly revealed for that member."""
    return [r for r in _pending_orphans(s) if r.item.owner_id == owner.id]


def owners_needing_attention(s: Session, viewer: User) -> set[int]:
    """Ids of members with undismissed warnings, for a neutral marker that names no item
    or reserver. The viewer's own items never count: even the marker would tell them a
    gift of theirs was reserved."""
    return {r.item.owner_id for r in _pending_orphans(s)} - {viewer.id}


def orphan_state(s: Session, item: Item) -> bool:
    """Whether the item has an orphaned reservation, judged on fresh database state
    (sessions do not expire on commit, so loaded relationships may be stale)."""
    s.refresh(item, ["deleted_at", "wishlists", "reservation"])
    return item.reservation is not None and is_orphaned(item.reservation)


def notify_orphan(
    s: Session, item: Item, mailer: Mailer | None, *, was_orphaned: bool, base_url: str
) -> None:
    """Email every admin except the item's owner when the item has just become orphaned
    (was_orphaned is orphan_state taken before the change). The email is neutral: it names
    the member and links their admin page, never the item or the reserver. Never emails
    for a dismissed warning. Delivery errors are logged, never raised."""
    if was_orphaned or not orphan_state(s, item) or mailer is None:
        return
    r = item.reservation
    if r.warning_dismissed_at is not None:
        return
    admins = s.scalars(select(User).where(User.is_admin.is_(True), User.id != item.owner_id))
    link = f"{base_url}/admin/members/{item.owner_id}"
    for admin in list(admins):
        try:
            subject, body = orphan_email(admin, item.owner, link)
            mailer.send(admin.email, subject, body)
        except DeliveryError as e:
            log.warning("orphan notification to admin %s failed: %s", admin.id, e)


def reset_dismissal(item: Item) -> None:
    """The item has a list again: a dismissed warning must not outlive the orphaning it
    was dismissed for, or the next orphaning would pass unnoticed."""
    if item.reservation is not None:
        item.reservation.warning_dismissed_at = None


def cancel_by_admin(s: Session, r: Reservation) -> None:
    item = r.item
    s.delete(r)
    image = None
    if item.deleted_at is not None:
        image = item.image
        s.delete(item)
    s.commit()
    if image:
        try:
            images.delete(image)
        except OSError:
            log.warning("could not delete picture file %s", image, exc_info=True)


def dismiss_warning(s: Session, r: Reservation) -> None:
    r.warning_dismissed_at = utcnow()
    s.commit()
