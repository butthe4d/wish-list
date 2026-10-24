from functools import wraps

from flask import Blueprint, redirect, render_template, session, url_for

from .db import get_session
from .keys import hash_key
from .models import User

bp = Blueprint("auth", __name__)


def current_user() -> User | None:
    uid = session.get("uid")
    if uid is None:
        return None
    user = get_session().get(User, uid)
    if user is None or session.get("kv") != user.key_version:
        return None
    return user


def member_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return render_template("welcome.html"), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @member_required
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user().is_admin:
            return render_template("link_invalid.html"), 404
        return view(*args, **kwargs)

    return wrapped


@bp.get("/u/<key>")
def personal_link(key):
    user = get_session().query(User).filter_by(key_hash=hash_key(key)).one_or_none()
    if user is None:
        return render_template("link_invalid.html"), 404
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["kv"] = user.key_version
    return redirect(url_for("member.home"))
