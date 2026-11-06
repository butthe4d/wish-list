import os
from datetime import timedelta

from urllib.parse import urlsplit

from flask import Flask, flash, redirect, render_template, request
from flask_babel import gettext as _
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import get_session, init_db
from .logredact import RedactingFilter
from .urls import BoundedIntConverter


def create_app(config: Config | None = None, *, mailer=None, sms=None) -> Flask:
    if config is None:
        config = Config.from_env(os.environ)
    app = Flask(__name__)
    app.config["WISHLIST"] = config
    app.logger.addFilter(RedactingFilter())
    app.config["SECRET_KEY"] = config.secret_key
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.base_url.startswith("https://")
    app.url_map.converters["int"] = BoundedIntConverter
    init_db(app)
    from .notify import build_mailer, build_sms

    app.extensions["mailer"] = build_mailer(config, mailer)
    app.extensions["sms"] = build_sms(config, sms)
    # the token lives as long as the (year-long) session instead of expiring after an hour
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    CSRFProtect(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.after_request
    def security_headers(response):
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        from .i18n import safe_next

        flash(_("This page expired, please try again."))
        target = request.referrer or ""
        parts = urlsplit(target)
        if parts.netloc:
            target = (parts.path + ("?" + parts.query if parts.query else "")) if (
                parts.netloc == request.host
            ) else ""
        return redirect(safe_next(target))

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith(("/u/", "/s/", "/shared/")):
            return render_template("link_invalid.html"), 404
        return render_template("not_found.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        # Boosted (htmx) requests swap 5xx responses in, so show the app's own layout.
        try:
            get_session().rollback()
            return render_template("error.html"), 500
        except Exception:
            app.logger.exception("error page failed")
            return _("Something went wrong. Please try again."), 500

    @app.get("/healthz")
    def healthz():
        return "ok"

    from . import auth
    from .views import admin, images, member, shared
    from .i18n import init_i18n
    from .themes import DESIGNS, THEME_ICONS, THEMES, get_settings

    init_i18n(app)

    app.context_processor(lambda: {"current_user": auth.current_user})

    @app.context_processor
    def design_and_theme():
        settings = get_settings(get_session())
        return {"page_theme": settings.default_theme, "design": settings.design}

    # globals, not context: imported macros (_macros.html) only see globals
    app.jinja_env.globals.update(THEMES=THEMES, THEME_ICONS=THEME_ICONS, DESIGNS=DESIGNS)

    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(member.bp)
    app.register_blueprint(shared.bp)
    app.register_blueprint(images.bp)

    return app
