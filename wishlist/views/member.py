from flask import Blueprint, current_app, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from .. import catalog, images, pictures, sharing
from ..auth import current_user, member_required
from ..db import get_session
from ..errors import ValidationError
from ..notify import DeliveryError, get_mailer, get_sms
from ..models import Item
from ..themes import get_settings
from ..urls import MAX_ID

bp = Blueprint("member", __name__)

FIND_LIMIT = 3  # picture candidates shown on the item form


def _form_ids(field: str) -> list[int]:
    try:
        ids = [int(v) for v in request.form.getlist(field)]
    except ValueError:
        abort(404)
    if any(not 0 < i <= MAX_ID for i in ids):
        abort(404)
    return ids


def _form_wishlist_ids() -> list[int]:
    return _form_ids("wishlist_ids")


def _candidate_tokens(form) -> list[str]:
    """Temp pictures found earlier for this form that still exist (ignores anything else)."""
    tokens = []
    for token in form.getlist("candidate"):
        if token not in tokens and images.temp_path(token) is not None:
            tokens.append(token)
    return tokens[:FIND_LIMIT]


def _render_form(user, item, form, error=None, *, candidates=None, notice=None, picture=None):
    selected = {int(v) for v in form.getlist("wishlist_ids") if v.isdigit()}
    candidates = _candidate_tokens(form) if candidates is None else candidates
    options = {f"temp:{t}" for t in candidates} | {"url", "none"}
    if item is not None and item.image:
        options.add("keep")
    if picture not in options:
        picture = form.get("picture")
    if picture not in options:
        picture = "keep" if "keep" in options else "none"
    return render_template(
        "item_form.html", item=item, form=form, error=error, selected=selected,
        wishlists=user.wishlists, candidates=candidates, notice=notice, picture=picture,
    )


def _find_pictures(user, item, form):
    """The "Find pictures" button: search the shop page and re-render with candidates."""
    try:
        link = catalog.normalize_link(form.get("link"))
    except ValidationError as e:
        return _render_form(user, item, form, e)
    if link is None:
        return _render_form(user, item, form, ValidationError("link", _("Paste a shop link first.")))
    if not pictures.allow_fetch(user.id):
        return _render_form(
            user, item, form, notice=_("Too many picture searches, try again later.")
        )
    try:
        found = pictures.find_pictures(link, limit=FIND_LIMIT)
    except pictures.PictureError:
        found = []
    except Exception:  # a hostile page must never turn into a 500
        current_app.logger.exception("picture search failed")
        found = []
    if not found:
        return _render_form(user, item, form, notice=_("Couldn't find pictures on that page."))
    tokens = [images.store_temp(data) for data in found]
    keep_current = item is not None and item.image
    return _render_form(
        user, item, form, candidates=tokens, picture="keep" if keep_current else f"temp:{tokens[0]}"
    )


def _image_choice(form) -> catalog.ImageChoice:
    picture = form.get("picture", "")
    if picture == "none":
        return ("none",)
    if picture == "url":
        url = form.get("image_url", "").strip()
        if not url:
            raise ValidationError("image", _("Paste a direct link to a picture."))
        return ("url", url)
    if picture.startswith("temp:"):
        return ("temp", picture.removeprefix("temp:"))
    return ("keep",)


def _save_item(user, item, form):
    s = get_session()
    fields = dict(
        name=form.get("name", ""), description=form.get("description"), link=form.get("link"),
        wishlist_ids=_form_wishlist_ids(),
    )
    try:
        choice = _image_choice(form)
    except ValidationError as e:
        return _render_form(user, item, form, e)
    if choice[0] == "url" and not pictures.allow_fetch(user.id):
        # a pasted link is a server-side fetch too: same per-member limit as "Find pictures"
        return _render_form(
            user, item, form, notice=_("Too many picture searches, try again later.")
        )
    try:
        if item is None:
            item = catalog.create_item(s, user, **fields)
        else:
            catalog.update_item(s, item, **fields)
    except ValidationError as e:
        s.rollback()
        return _render_form(user, item, form, e)
    try:
        catalog.set_item_image(s, item, choice)
    except ValidationError as e:
        # The item itself is saved; show its edit form so a re-submit doesn't duplicate it.
        return _render_form(user, item, form, e)
    return redirect(url_for("member.home"))


def _render_home(user, error=None, form=None, status=200):
    s = get_session()
    items = list(
        s.scalars(
            select(Item)
            .where(Item.owner_id == user.id, Item.deleted_at.is_(None))
            .order_by(Item.created_at, Item.id)
        )
    )
    return (
        render_template(
            "home.html", user=user, wishlists=user.wishlists, items=items, error=error,
            form=form or {}, default_theme=get_settings(s).default_theme,
        ),
        status,
    )


@bp.get("/")
@member_required
def home():
    return _render_home(current_user())


@bp.route("/items/new", methods=["GET", "POST"])
@member_required
def item_new():
    user = current_user()
    if request.method == "POST":
        if request.form.get("action") == "find":
            return _find_pictures(user, None, request.form)
        return _save_item(user, None, request.form)
    return _render_form(user, None, request.form)


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@member_required
def item_edit(item_id):
    user = current_user()
    item = catalog.get_owned_item(get_session(), user, item_id)
    if request.method == "POST":
        if request.form.get("action") == "find":
            return _find_pictures(user, item, request.form)
        return _save_item(user, item, request.form)
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


@bp.post("/lists")
@member_required
def list_create():
    user = current_user()
    s = get_session()
    try:
        wl = catalog.create_wishlist(
            s, user, name=request.form.get("name", ""),
            description=request.form.get("description"),
            theme=request.form.get("theme"),
        )
    except ValidationError as e:
        return _render_home(user, e, request.form)
    return redirect(url_for("member.list_view", wl_id=wl.id))


def _render_list(user, wl, error=None, form=None, status=200):
    in_list = {i.id for i in wl.items}
    live = [i for i in wl.items if i.deleted_at is None]
    available = [
        i
        for i in get_session().scalars(
            select(Item)
            .where(Item.owner_id == user.id, Item.deleted_at.is_(None))
            .order_by(Item.created_at, Item.id)
        )
        if i.id not in in_list
    ]
    return (
        render_template(
            "list_owner.html", wl=wl, items=live, available=available, share_url=sharing.wishlist_share_link(wl),
            share_logs=sorted(wl.share_logs, key=lambda l: (l.sent_at, l.id), reverse=True),
            can_email=get_mailer() is not None, can_sms=get_sms() is not None,
            error=error, form=form or {}, theme=wl.theme,
        ),
        status,
    )


@bp.get("/lists/<int:wl_id>")
@member_required
def list_view(wl_id):
    user = current_user()
    return _render_list(user, catalog.get_owned_wishlist(get_session(), user, wl_id))


@bp.post("/lists/<int:wl_id>/edit")
@member_required
def list_edit(wl_id):
    user = current_user()
    s = get_session()
    wl = catalog.get_owned_wishlist(s, user, wl_id)
    try:
        catalog.update_wishlist(
            s, wl, name=request.form.get("name", ""),
            description=request.form.get("description"),
            theme=request.form.get("theme", wl.theme),
        )
    except ValidationError as e:
        s.rollback()
        return _render_list(user, wl, e, request.form)
    return redirect(url_for("member.list_view", wl_id=wl.id))


@bp.post("/lists/<int:wl_id>/delete")
@member_required
def list_delete(wl_id):
    s = get_session()
    catalog.delete_wishlist(s, catalog.get_owned_wishlist(s, current_user(), wl_id))
    return redirect(url_for("member.home"))


@bp.post("/lists/<int:wl_id>/items")
@member_required
def list_add_items(wl_id):
    s = get_session()
    wl = catalog.get_owned_wishlist(s, current_user(), wl_id)
    catalog.add_items_to_wishlist(s, wl, _form_ids("item_id"))
    return redirect(url_for("member.list_view", wl_id=wl.id))


@bp.post("/lists/<int:wl_id>/items/<int:item_id>/remove")
@member_required
def list_remove_item(wl_id, item_id):
    user = current_user()
    s = get_session()
    wl = catalog.get_owned_wishlist(s, user, wl_id)
    catalog.remove_item_from_wishlist(s, wl, catalog.get_owned_item(s, user, item_id))
    return redirect(url_for("member.list_view", wl_id=wl.id))


@bp.post("/lists/<int:wl_id>/share/email")
@member_required
def share_email(wl_id):
    user = current_user()
    s = get_session()
    wl = catalog.get_owned_wishlist(s, user, wl_id)
    mailer = get_mailer()
    if mailer is None:
        abort(404)
    try:
        recipients = sharing.parse_emails(request.form.get("emails", ""))
    except ValidationError as e:
        flash(e.message)
        return redirect(url_for("member.list_view", wl_id=wl.id))
    try:
        failed = sharing.share_by_email(s, wl, user, recipients, mailer)
    except sharing.RateLimited:
        flash(_("Daily sending limit reached, try again tomorrow."))
        return redirect(url_for("member.list_view", wl_id=wl.id))
    sent = [r for r in recipients if r not in failed]
    if sent:
        flash(_("Sent to %(recipients)s.", recipients=", ".join(sent)))
    if failed:
        flash(_("Couldn't send to %(recipients)s.", recipients=", ".join(failed)))
    return redirect(url_for("member.list_view", wl_id=wl.id))


@bp.post("/lists/<int:wl_id>/share/sms")
@member_required
def share_sms(wl_id):
    user = current_user()
    s = get_session()
    wl = catalog.get_owned_wishlist(s, user, wl_id)
    sms = get_sms()
    if sms is None:
        abort(404)
    try:
        phone = sharing.normalize_phone(request.form.get("phone", ""))
        sharing.share_by_sms(s, wl, user, phone, sms)
    except ValidationError as e:
        flash(e.message)
    except sharing.RateLimited:
        flash(_("Daily sending limit reached, try again tomorrow."))
    except DeliveryError:
        flash(_("Couldn't send the text message."))
    else:
        flash(_("Sent to %(recipients)s.", recipients=phone))
    return redirect(url_for("member.list_view", wl_id=wl.id))


@bp.post("/lists/<int:wl_id>/share/regenerate")
@member_required
def share_regenerate(wl_id):
    s = get_session()
    wl = catalog.get_owned_wishlist(s, current_user(), wl_id)
    sharing.regenerate_share_key(s, wl)
    flash(_("New share link created. The old link no longer works."))
    return redirect(url_for("member.list_view", wl_id=wl.id))
