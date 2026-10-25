from __future__ import annotations

import logging
import re
from datetime import timedelta

from flask import current_app
from flask_babel import gettext as _
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .errors import ValidationError
from .keys import parse_share_key, share_key, share_link, verify_share_key
from .messages import share_email, share_sms
from .models import ShareLog, User, Wishlist, utcnow
from .notify import DeliveryError, Mailer, SmsSender
from .urls import MAX_ID

log = logging.getLogger(__name__)


def open_share(s: Session, secret: str, key: str) -> Wishlist | None:
    wl_id = parse_share_key(key)
    if wl_id is None or wl_id > MAX_ID:
        return None
    wl = s.get(Wishlist, wl_id)
    if wl is None or not verify_share_key(secret, key, wl.id, wl.share_version):
        return None
    return wl


def regenerate_share_key(s: Session, wl: Wishlist) -> None:
    wl.share_version += 1
    s.commit()


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+\d{8,15}$")
MAX_EMAILS = 10


def parse_emails(text: str) -> list[str]:
    found = list(dict.fromkeys(p for p in re.split(r"[,;\s]+", text) if p))
    if not found:
        raise ValidationError("emails", _("Enter at least one email address."))
    if len(found) > MAX_EMAILS:
        raise ValidationError("emails", _("Enter at most %(n)d email addresses.", n=MAX_EMAILS))
    bad = [e for e in found if not EMAIL_RE.match(e)]
    if bad:
        raise ValidationError(
            "emails", _("This is not a valid email address: %(address)s", address=bad[0])
        )
    return found


def normalize_phone(text: str) -> str:
    phone = re.sub(r"[\s\-()]", "", text)
    if not _PHONE_RE.match(phone):
        raise ValidationError(
            "phone", _("Use international format, e.g. +48600100200.")
        )
    return phone


def _log_share(s: Session, wl: Wishlist, sharer: User, channel: str, recipient: str) -> None:
    s.add(ShareLog(sharer_id=sharer.id, wishlist_id=wl.id, channel=channel, recipient=recipient))


def wishlist_share_link(wl: Wishlist) -> str:
    cfg = current_app.config["WISHLIST"]
    return share_link(cfg.base_url, share_key(cfg.secret_key, wl.id, wl.share_version))


MAX_EMAILS_PER_DAY = 50
MAX_SMS_PER_DAY = 20


class RateLimited(Exception):
    pass


def _sent_last_day(s: Session, sharer: User, channel: str) -> int:
    return s.scalar(
        select(func.count(ShareLog.id))
        .where(
            ShareLog.sharer_id == sharer.id,
            ShareLog.channel == channel,
            ShareLog.sent_at > utcnow() - timedelta(hours=24),
        )
    )


def share_by_email(
    s: Session, wl: Wishlist, sharer: User, recipients: list[str], mailer: Mailer
) -> list[str]:
    if _sent_last_day(s, sharer, "email") + len(recipients) > MAX_EMAILS_PER_DAY:
        raise RateLimited()
    link = wishlist_share_link(wl)
    subject, body = share_email(sharer, wl, link)
    failed = []
    for to in recipients:
        try:
            mailer.send(to, subject, body)
        except DeliveryError as e:
            log.warning("share email to %s failed: %s", to, e)
            failed.append(to)
        else:
            _log_share(s, wl, sharer, "email", to)
    s.commit()
    return failed


def share_by_sms(s: Session, wl: Wishlist, sharer: User, phone: str, sms: SmsSender) -> None:
    if _sent_last_day(s, sharer, "sms") >= MAX_SMS_PER_DAY:
        raise RateLimited()
    sms.send(phone, share_sms(sharer, wl, wishlist_share_link(wl)))
    _log_share(s, wl, sharer, "sms", phone)
    s.commit()
