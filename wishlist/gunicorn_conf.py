"""Gunicorn settings. Used as `gunicorn -c python:wishlist.gunicorn_conf "wishlist:create_app()"`."""

import re

from gunicorn.glogging import Logger

bind = "0.0.0.0:8000"
workers = 2
threads = 4
accesslog = "-"

_KEY_PATH = re.compile(r"(/[us]/)[^/?#\s]+")


def redact_keys(text: str) -> str:
    """Replace the key in `/u/<key>` and `/s/<key>` so links never reach the logs."""
    return _KEY_PATH.sub(r"\1***", text)


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        return {k: redact_keys(v) if isinstance(v, str) else v for k, v in atoms.items()}


logger_class = RedactingLogger
