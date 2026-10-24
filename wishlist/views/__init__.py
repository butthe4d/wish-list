"""TEMPORARY placeholder routes; Tasks 5 and 9 replace them."""

from flask import Blueprint, render_template
from flask_babel import gettext as _

from ..auth import admin_required, member_required

bp = Blueprint("placeholder", __name__)


@bp.get("/")
@member_required
def home():
    return render_template("placeholder.html", title=_("Home (placeholder)"))


@bp.get("/admin/")
@admin_required
def admin_home():
    return render_template("placeholder.html", title=_("Admin (placeholder)"))
