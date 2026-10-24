from flask import Blueprint, abort, current_app, make_response, redirect, request
from flask_babel import Babel, get_locale

from .auth import current_user
from .config import SUPPORTED_LANGUAGES
from .db import get_session

bp = Blueprint("i18n", __name__)
babel = Babel()

LANGUAGE_NAMES = {"en": "English", "pl": "Polski"}
COOKIE_MAX_AGE = 365 * 24 * 3600


def select_locale() -> str:
    user = current_user()
    if user is not None and user.language in SUPPORTED_LANGUAGES:
        return user.language
    cookie = request.cookies.get("lang")
    if cookie in SUPPORTED_LANGUAGES:
        return cookie
    best = request.accept_languages.best_match(SUPPORTED_LANGUAGES)
    if best:
        return best
    return current_app.config["WISHLIST"].default_language


def safe_next(target: str | None) -> str:
    """Only same-site relative paths: one leading '/', no '//' or '/\\', no control chars."""
    if (
        target
        and target.startswith("/")
        and target[1:2] not in ("/", "\\")
        and all(ord(c) >= 32 and c != "\x7f" for c in target)
    ):
        return target
    return "/"


@bp.post("/lang")
def set_language():
    lang = request.form.get("lang")
    if lang not in SUPPORTED_LANGUAGES:
        abort(400)
    user = current_user()
    if user is not None:
        user.language = lang
        get_session().commit()
    response = make_response(redirect(safe_next(request.form.get("next"))))
    response.set_cookie(
        "lang",
        lang,
        max_age=COOKIE_MAX_AGE,
        samesite="Lax",
        secure=current_app.config["SESSION_COOKIE_SECURE"],
    )
    return response


def init_i18n(app) -> None:
    app.config.setdefault("BABEL_DEFAULT_LOCALE", app.config["WISHLIST"].default_language)
    babel.init_app(app, locale_selector=select_locale)
    app.register_blueprint(bp)
    app.context_processor(lambda: {"get_locale": lambda: str(get_locale())})
