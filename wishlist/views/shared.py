from flask import (
    Blueprint, abort, current_app, make_response, redirect, render_template, request, session,
    url_for,
)
from flask_babel import gettext as _

from .. import reservations, sharing
from ..auth import current_user
from ..db import get_session
from ..errors import ValidationError
from ..keys import new_key
from ..models import Item, Wishlist

bp = Blueprint("shared", __name__)

GUEST_COOKIE = "guest"


def _set_guest_cookie(response):
    response.set_cookie(
        GUEST_COOKIE, new_key(), max_age=365 * 24 * 3600, httponly=True, samesite="Lax",
        secure=request.is_secure,
    )
    return response


def _is_owner(wl: Wishlist) -> bool:
    user = current_user()
    return user is not None and user.id == wl.owner_id


def _shared_list(wl_id: int) -> Wishlist:
    wl = get_session().get(Wishlist, wl_id)
    if wl is None or session.get("shares", {}).get(str(wl_id)) != wl.share_version:
        abort(404)
    return wl


def _shared_item(wl: Wishlist, item_id: int) -> Item:
    item = get_session().get(Item, item_id)
    if item is None or item.deleted_at is not None or wl not in item.wishlists:
        abort(404)
    return item


def _render(wl, error=None, error_item_id=None, notice=None, form=None, status=200):
    token = request.cookies.get(GUEST_COOKIE)
    items = [i for i in wl.items if i.deleted_at is None]
    rows = [(i, reservations.state_for_guest(i, token)) for i in items]
    response = make_response(
        render_template(
            "shared_list.html", wl=wl, owner_first_name=wl.owner.name.split()[0], rows=rows,
            error=error, error_item_id=error_item_id, notice=notice, form=form or {},
        ),
        status,
    )
    response.headers["Cache-Control"] = "no-store"
    if token is None:
        _set_guest_cookie(response)
    return response


@bp.get("/s/<key>")
def open_link(key):
    cfg = current_app.config["WISHLIST"]
    wl = sharing.open_share(get_session(), cfg.secret_key, key)
    if wl is None:
        return render_template("link_invalid.html"), 404
    if _is_owner(wl):
        return redirect(url_for("member.list_view", wl_id=wl.id))
    shares = dict(session.get("shares", {}))
    shares[str(wl.id)] = wl.share_version
    session["shares"] = shares
    session.permanent = True
    response = redirect(url_for("shared.view", wl_id=wl.id))
    if GUEST_COOKIE not in request.cookies:
        _set_guest_cookie(response)
    return response


@bp.get("/shared/<int:wl_id>")
def view(wl_id):
    wl = _shared_list(wl_id)
    if _is_owner(wl):
        return redirect(url_for("member.list_view", wl_id=wl.id))
    return _render(wl)


@bp.post("/shared/<int:wl_id>/items/<int:item_id>/reserve")
def reserve(wl_id, item_id):
    wl = _shared_list(wl_id)
    if _is_owner(wl):
        return redirect(url_for("member.list_view", wl_id=wl.id))
    item = _shared_item(wl, item_id)
    token = request.cookies.get(GUEST_COOKIE)
    if token is None:  # cookie was cleared; the page re-issues it
        return redirect(url_for("shared.view", wl_id=wl.id))
    s = get_session()
    try:
        reservations.reserve(
            s, item, wl, reserver_name=request.form.get("name", ""), guest_token=token
        )
    except ValidationError as e:
        return _render(wl, error=e, error_item_id=item.id, form=request.form, status=400)
    except reservations.AlreadyReserved:
        s.expire_all()
        return _render(wl, notice=_("Sorry, someone just reserved that item."), status=409)
    return redirect(url_for("shared.view", wl_id=wl.id))


@bp.post("/shared/<int:wl_id>/items/<int:item_id>/cancel")
def cancel(wl_id, item_id):
    wl = _shared_list(wl_id)
    if _is_owner(wl):
        return redirect(url_for("member.list_view", wl_id=wl.id))
    item = _shared_item(wl, item_id)
    token = request.cookies.get(GUEST_COOKIE)
    if token:
        reservations.cancel_by_guest(get_session(), item, token)
    return redirect(url_for("shared.view", wl_id=wl.id))
