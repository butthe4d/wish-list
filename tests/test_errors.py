import pytest

from wishlist import create_app


@pytest.fixture
def failing_app(app_config, app):  # `app` runs the migrations
    a = create_app(app_config)
    a.config.update(WTF_CSRF_ENABLED=False)  # TESTING off: errors reach the handler

    @a.get("/boom")
    def boom():
        raise RuntimeError("boom")

    return a


def test_server_error_page_uses_app_layout(failing_app):
    r = failing_app.test_client().get("/boom")
    assert r.status_code == 500
    assert "Something went wrong. Please try again." in r.text
    assert 'class="page"' in r.text and "design-a.css" in r.text
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_server_error_page_in_polish(failing_app):
    r = failing_app.test_client().get("/boom", headers={"Accept-Language": "pl"})
    assert r.status_code == 500 and "Coś poszło nie tak. Spróbuj ponownie." in r.text
