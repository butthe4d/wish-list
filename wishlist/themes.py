from __future__ import annotations

from flask_babel import gettext as _
from flask_babel import lazy_gettext
from sqlalchemy.orm import Session

from .errors import ValidationError
from .models import AppSettings

THEMES = {
    "classic": lazy_gettext("Classic"),
    "christmas": lazy_gettext("Christmas"),
    "birthday": lazy_gettext("Birthday"),
}
DESIGNS = {"a": lazy_gettext("A — tiles")}

DEFAULT_DESIGN = "a"
DEFAULT_THEME = "classic"


def validate_theme(key: str) -> str:
    if key not in THEMES:
        raise ValidationError("theme", _("Unknown theme."))
    return key


def get_settings(s: Session) -> AppSettings:
    """Read-only: the row is seeded by migration 0002. If it is somehow missing, return an
    unsaved default (never added to the session, never committed)."""
    st = s.get(AppSettings, 1)
    if st is None:
        st = AppSettings(id=1, design=DEFAULT_DESIGN, default_theme=DEFAULT_THEME)
    return st


def update_settings(s: Session, *, design: str, default_theme: str) -> AppSettings:
    if design not in DESIGNS:
        raise ValidationError("design", _("Unknown design."))
    if default_theme not in THEMES:
        raise ValidationError("default_theme", _("Unknown theme."))
    st = get_settings(s)
    st.design, st.default_theme = design, default_theme
    s.add(st)
    s.commit()
    return st
