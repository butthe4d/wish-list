from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from .. import catalog
from ..auth import current_user, member_required
from ..db import get_session
from ..errors import ValidationError
from ..models import Item

bp = Blueprint("member", __name__)


_SQLITE_MAX_INT = 2**63 - 1


def _form_wishlist_ids() -> list[int]:
    try:
        ids = [int(v) for v in request.form.getlist("wishlist_ids")]
    except ValueError:
        abort(404)
    if any(not 0 < i <= _SQLITE_MAX_INT for i in ids):
        abort(404)
    return ids


def _render_form(user, item, form, error=None, status=200):
    selected = {int(v) for v in form.getlist("wishlist_ids") if v.isdigit()}
    return (
        render_template(
            "item_form.html", item=item, form=form, error=error, selected=selected,
            wishlists=user.wishlists,
        ),
        status,
    )


@bp.get("/")
@member_required
def home():
    user = current_user()
    s = get_session()
    items = list(
        s.scalars(
            select(Item)
            .where(Item.owner_id == user.id, Item.deleted_at.is_(None))
            .order_by(Item.created_at, Item.id)
        )
    )
    return render_template("home.html", user=user, wishlists=user.wishlists, items=items)


@bp.route("/items/new", methods=["GET", "POST"])
@member_required
def item_new():
    user = current_user()
    if request.method == "POST":
        try:
            catalog.create_item(
                get_session(),
                user,
                name=request.form.get("name", ""),
                description=request.form.get("description"),
                link=request.form.get("link"),
                wishlist_ids=_form_wishlist_ids(),
            )
        except ValidationError as e:
            return _render_form(user, None, request.form, e, 200)
        return redirect(url_for("member.home"))
    return _render_form(user, None, request.form)


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@member_required
def item_edit(item_id):
    user = current_user()
    s = get_session()
    item = catalog.get_owned_item(s, user, item_id)
    if request.method == "POST":
        try:
            catalog.update_item(
                s,
                item,
                name=request.form.get("name", ""),
                description=request.form.get("description"),
                link=request.form.get("link"),
                wishlist_ids=_form_wishlist_ids(),
            )
        except ValidationError as e:
            s.rollback()
            return _render_form(user, item, request.form, e, 200)
        return redirect(url_for("member.home"))
    form = MultiDict(
        [("name", item.name), ("description", item.description or ""), ("link", item.link or "")]
        + [("wishlist_ids", str(w.id)) for w in item.wishlists]
    )
    return _render_form(user, item, form)


@bp.post("/items/<int:item_id>/delete")
@member_required
def item_delete(item_id):
    s = get_session()
    catalog.delete_item(s, catalog.get_owned_item(s, current_user(), item_id))
    return redirect(url_for("member.home"))
