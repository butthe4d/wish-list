import pytest

from wishlist.gunicorn_conf import redact_keys

KEY = "abcDEF123_-abcDEF123_-abcDEF123_-abcDEF123_-"


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"/u/{KEY}", "/u/***"),
        (f"/s/{KEY}", "/s/***"),
        (f"/s/12.{KEY}", "/s/***"),
        (f"/u/{KEY}?next=%2F", "/u/***?next=%2F"),
        (f"GET /s/{KEY}?x=1 HTTP/1.1", "GET /s/*** HTTP/1.1".replace("***", "***?x=1")),
        ("/items/new", "/items/new"),
        ("/healthz", "/healthz"),
        ("/u/", "/u/"),
    ],
)
def test_redact_keys(text, expected):
    assert redact_keys(text) == expected
