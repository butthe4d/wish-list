import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config


def create_app(config: Config | None = None, *, mailer=None, sms=None) -> Flask:
    if config is None:
        config = Config.from_env(os.environ)
    app = Flask(__name__)
    app.config["WISHLIST"] = config
    app.config["SECRET_KEY"] = config.secret_key
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.after_request
    def security_headers(response):
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/healthz")
    def healthz():
        return "ok"

    return app
