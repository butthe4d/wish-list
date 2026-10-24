"""TEMPORARY placeholder admin route; Task 9 replaces it."""

from flask import Blueprint, render_template
from flask_babel import gettext as _

from ..auth import admin_required

bp = Blueprint("placeholder", __name__)


@bp.get("/admin/")
@admin_required
def admin_home():
    return render_template("placeholder.html", title=_("Admin (placeholder)"))
