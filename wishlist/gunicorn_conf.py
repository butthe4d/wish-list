"""Gunicorn settings. Used as `gunicorn -c python:wishlist.gunicorn_conf "wishlist:create_app()"`."""

from gunicorn.glogging import Logger

from wishlist.logredact import RedactingFilter, redact_keys

bind = "0.0.0.0:8000"
workers = 2
threads = 4
accesslog = "-"
control_socket_disable = True


class RedactingLogger(Logger):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.error_log.addFilter(RedactingFilter())

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        return {k: redact_keys(v) if isinstance(v, str) else v for k, v in atoms.items()}


logger_class = RedactingLogger
