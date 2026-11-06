from flask import Blueprint, abort, send_file
from sqlalchemy import select

from .. import images
from ..auth import member_required
from ..db import get_session
from ..models import Item

bp = Blueprint("images", __name__)


@bp.get("/images/tmp/<token>")
@member_required
def temp_image(token):
    path = images.temp_path(token)
    if path is None:
        abort(404)
    response = send_file(path, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/images/<name>")
def item_image(name):
    path = images.path_for(name)
    if path is None:
        abort(404)
    live = get_session().scalar(
        select(Item.id).where(Item.image == name, Item.deleted_at.is_(None))
    )
    if live is None:
        abort(404)
    response = send_file(path, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response
