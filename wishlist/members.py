from __future__ import annotations

import logging
import re

from flask import current_app
from flask_babel import gettext as _
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import SUPPORTED_LANGUAGES
from .errors import ValidationError
from .keys import hash_key, new_key, personal_link
from .messages import invite_email
from .models import User
from .notify import DeliveryError, Mailer
from .sharing import EMAIL_RE

log = logging.getLogger(__name__)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]")


def _validate(s: Session, *, name, email, language, exclude_id=None) -> tuple[str, str]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("name", _("Name is required."))
    if len(name) > 100:
        raise ValidationError("name", _("Name must be at most 100 characters."))
    if _CONTROL_RE.search(name):
        raise ValidationError("name", _("Name must not contain control characters."))
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValidationError("email", _("Enter a valid email address."))
    if language not in SUPPORTED_LANGUAGES:
        raise ValidationError("language", _("Choose a supported language."))
    q = select(User.id).where(User.email == email)
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    if s.scalar(q) is not None:
        raise ValidationError("email", _("A member with this email already exists."))
    return name, email


def _commit(s: Session) -> None:
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise ValidationError("email", _("A member with this email already exists.")) from None


def create_member(s: Session, *, name: str, email: str, language: str, is_admin: bool) -> tuple[User, str]:
    name, email = _validate(s, name=name, email=email, language=language)
    key = new_key()
    user = User(name=name, email=email, language=language, is_admin=is_admin, key_hash=hash_key(key))
    s.add(user)
    _commit(s)
    return user, key


def _admin_count(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True)))


def update_member(
    s: Session, user: User, *, name: str, email: str, language: str, is_admin: bool
) -> User:
    name, email = _validate(s, name=name, email=email, language=language, exclude_id=user.id)
    if user.is_admin and not is_admin and _admin_count(s) <= 1:
        raise ValidationError("is_admin", _("There must be at least one admin."))
    user.name, user.email, user.language, user.is_admin = name, email, language, is_admin
    _commit(s)
    return user


def delete_member(s: Session, user: User, acting_admin: User) -> None:
    if user.id == acting_admin.id:
        raise ValidationError("delete", _("You can't delete yourself."))
    s.delete(user)
    s.commit()


def rotate_key(s: Session, user: User) -> str:
    key = new_key()
    user.key_hash = hash_key(key)
    user.key_version += 1
    s.commit()
    return key


def send_link(user: User, key: str, mailer: Mailer | None) -> bool:
    """Email the personal link. True if sent; False if no mailer or delivery failed."""
    if mailer is None:
        return False
    link = personal_link(current_app.config["WISHLIST"].base_url, key)
    subject, body = invite_email(user, link)
    try:
        mailer.send(user.email, subject, body)
    except DeliveryError as e:
        log.warning("invite email to %s failed: %s", user.email, e)
        return False
    return True
