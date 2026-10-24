from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_babel import gettext as _

from .. import members
from ..auth import admin_required, current_user
from ..config import SUPPORTED_LANGUAGES
from ..db import get_session
from ..errors import ValidationError
from ..i18n import LANGUAGE_NAMES
from ..keys import personal_link
from ..models import User
from ..notify import get_mailer

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_member(uid: int) -> User:
    user = get_session().get(User, uid)
    if user is None:
        abort(404)
    return user


def _render_form(member, form, error=None):
    return render_template(
        "admin/member_form.html", member=member, form=form, error=error,
        languages=[(c, LANGUAGE_NAMES.get(c, c)) for c in SUPPORTED_LANGUAGES],
    ), 200


def _render_link_once(user, key):
    emailed = members.send_link(user, key, get_mailer())
    link = personal_link(current_app.config["WISHLIST"].base_url, key)
    response = make_response(
        render_template(
            "admin/link_once.html", member=user, link=link, emailed=emailed,
            has_mailer=get_mailer() is not None,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/")
@admin_required
def index():
    users = get_session().query(User).order_by(User.name, User.id).all()
    return render_template("admin/index.html", members=users, me=current_user(), warnings=[])


@bp.route("/members/new", methods=["GET", "POST"])
@admin_required
def member_new():
    if request.method == "POST":
        try:
            user, key = members.create_member(
                get_session(),
                name=request.form.get("name", ""),
                email=request.form.get("email", ""),
                language=request.form.get("language", ""),
                is_admin=request.form.get("is_admin") == "on",
            )
        except ValidationError as e:
            return _render_form(None, request.form, e)
        return _render_link_once(user, key)
    return _render_form(None, {"language": "en"})


@bp.route("/members/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def member_edit(uid):
    user = _get_member(uid)
    s = get_session()
    if request.method == "POST":
        try:
            members.update_member(
                s, user,
                name=request.form.get("name", ""),
                email=request.form.get("email", ""),
                language=request.form.get("language", ""),
                is_admin=request.form.get("is_admin") == "on",
                acting_admin=current_user(),
            )
        except ValidationError as e:
            s.rollback()
            return _render_form(user, request.form, e)
        flash(_("Saved."))
        return redirect(url_for("admin.index"))
    return _render_form(
        user,
        {"name": user.name, "email": user.email, "language": user.language,
         "is_admin": "on" if user.is_admin else ""},
    )


@bp.post("/members/<int:uid>/delete")
@admin_required
def member_delete(uid):
    user = _get_member(uid)
    if request.form.get("confirm") != "yes":
        flash(_("Tick the confirmation box to delete a member."))
        return redirect(url_for("admin.index"))
    try:
        members.delete_member(get_session(), user, current_user())
    except ValidationError as e:
        flash(e.message)
        return redirect(url_for("admin.index"))
    flash(_("Member deleted."))
    return redirect(url_for("admin.index"))


@bp.post("/members/<int:uid>/rotate-key")
@admin_required
def member_rotate_key(uid):
    user = _get_member(uid)
    key = members.rotate_key(get_session(), user)
    return _render_link_once(user, key)
