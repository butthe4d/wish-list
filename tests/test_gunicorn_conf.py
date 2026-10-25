import datetime
import logging
from types import SimpleNamespace

import pytest
from gunicorn.config import Config as GunicornConfig

from wishlist.gunicorn_conf import RedactingLogger, redact_keys

KEY = "abcDEF123_-abcDEF123_-abcDEF123_-abcDEF123_-"


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"/u/{KEY}", "/u/***"),
        (f"/s/{KEY}", "/s/***"),
        (f"/s/12.{KEY}", "/s/***"),
        (f"/u/{KEY}?next=%2F", "/u/***?next=%2F"),
        (f"GET /s/{KEY}?x=1 HTTP/1.1", "GET /s/***?x=1 HTTP/1.1"),
        ("/items/new", "/items/new"),
        ("/healthz", "/healthz"),
        ("/u/", "/u/"),
    ],
)
def test_redact_keys(text, expected):
    assert redact_keys(text) == expected


def test_logger_atoms_contain_no_key():
    logger = RedactingLogger(GunicornConfig())
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": f"/u/{KEY}?x=1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "PATH_INFO": f"/u/{KEY}",
        "QUERY_STRING": "x=1",
        "HTTP_REFERER": f"https://wish.example/s/{KEY}",
    }
    req = SimpleNamespace(headers=[("Referer", f"https://wish.example/s/{KEY}")])
    resp = SimpleNamespace(status="302 FOUND", headers=[], sent=0)
    atoms = logger.atoms(resp, req, environ, datetime.timedelta(0))
    assert atoms["r"] == "GET /u/***?x=1 HTTP/1.1"
    assert atoms["U"] == "/u/***"
    assert atoms["f"] == "https://wish.example/s/***"
    assert atoms["{referer}i"] == "https://wish.example/s/***"
    assert KEY not in "".join(str(v) for v in atoms.values())


def test_error_log_redacts_keys(caplog):
    logger = RedactingLogger(GunicornConfig())
    handler = logging.Handler()
    seen = []
    handler.emit = lambda record: seen.append(record.getMessage())
    logger.error_log.addHandler(handler)
    logger.error_log.setLevel(logging.ERROR)
    logger.error(f"bad request line GET /s/{KEY} HTTP/1.1")
    assert seen and KEY not in seen[0] and "/s/***" in seen[0]
