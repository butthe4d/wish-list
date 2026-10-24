import os
from datetime import timedelta

from flask import Flask
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import init_db


def create_app(config: Config | None = None, *, mailer=None, sms=None) -> Flask:
    if config is None:
        config = Config.from_env(os.environ)
    app = Flask(__name__)
    app.config["WISHLIST"] = config
    app.config["SECRET_KEY"] = config.secret_key
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.base_url.startswith("https://")
    init_db(app)
    CSRFProtect(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.after_request
    def security_headers(response):
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/healthz")
    def healthz():
        return "ok"

    from . import auth, views
    from .i18n import init_i18n

    init_i18n(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)

    return app
