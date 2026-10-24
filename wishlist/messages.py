from __future__ import annotations

import re

from flask_babel import force_locale
from flask_babel import gettext as _

from .models import Reservation, User, Wishlist


def _one_line(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", text).strip()


def invite_email(user: User, link: str) -> tuple[str, str]:
    with force_locale(user.language):
        subject = _("You're invited to the wishlist")
        body = _(
            "Hi %(name)s,\n\nYou have been invited to the household wishlist. "
            "Open this personal link to sign in:\n%(link)s\n\n"
            "Keep the link private: anyone who has it can act as you.",
            name=user.name,
            link=link,
        )
    return subject, body


def share_email(sharer: User, wl: Wishlist, link: str) -> tuple[str, str]:
    with force_locale(sharer.language):
        subject = _(
            "%(name)s shared a wishlist with you: %(list)s",
            name=_one_line(sharer.name),
            list=_one_line(wl.name),
        )
        body = _(
            "Hi,\n\n%(name)s shared the wishlist \"%(list)s\" with you. "
            "Open it here to see the items and reserve a gift:\n%(link)s",
            name=sharer.name,
            list=wl.name,
            link=link,
        )
    return subject, body


def share_sms(sharer: User, wl: Wishlist, link: str) -> str:
    with force_locale(sharer.language):
        return _(
            "%(name)s shared the wishlist \"%(list)s\" with you: %(link)s",
            name=sharer.name,
            list=wl.name,
            link=link,
        )


def orphan_email(admin: User, r: Reservation) -> tuple[str, str]:
    with force_locale(admin.language):
        subject = _("A reserved item was changed: %(item)s", item=_one_line(r.item.name))
        body = _(
            "Hi %(admin)s,\n\n%(owner)s changed or removed \"%(item)s\", "
            "which %(reserver)s had reserved via the list \"%(list)s\". "
            "You may want to let %(reserver)s know.",
            admin=admin.name,
            owner=r.item.owner.name,
            item=r.item.name,
            reserver=r.reserver_name,
            list=r.via_wishlist_name,
        )
    return subject, body
