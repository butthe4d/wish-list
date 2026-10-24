import smtplib

import pytest

import wishlist.notify as notify
from wishlist.catalog import create_wishlist
from wishlist.messages import share_sms
from wishlist.notify import DeliveryError, GsmServiceSms, SmtpMailer


def _config(app_config, **kw):
    from dataclasses import replace

    return replace(
        app_config, smtp_host="smtp.example", smtp_port=2525, smtp_user="u", smtp_password="p",
        smtp_from="wish@example.com", **kw,
    )


class FakeSmtp:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        self.message = None
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, *a, **k):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append("send")
        self.message = msg


def test_smtp_mailer_starttls(monkeypatch, app_config):
    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    SmtpMailer(_config(app_config)).send("to@x.pl", "Hi żółw", "Body")
    smtp = FakeSmtp.instances[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example", 2525, 15)
    assert smtp.calls == ["starttls", ("login", "u", "p"), "send"]
    assert smtp.message["To"] == "to@x.pl"
    assert smtp.message["From"] == "wish@example.com"
    assert smtp.message["Subject"] == "Hi żółw"
    assert smtp.message.get_content().strip() == "Body"


def test_smtp_mailer_ssl_and_none(monkeypatch, app_config):
    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    SmtpMailer(_config(app_config, smtp_security="ssl")).send("a@x.pl", "s", "b")
    assert "starttls" not in FakeSmtp.instances[0].calls
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    SmtpMailer(_config(app_config, smtp_security="none")).send("a@x.pl", "s", "b")
    assert FakeSmtp.instances[1].calls[-1] == "send" and "starttls" not in FakeSmtp.instances[1].calls


def test_smtp_failure_becomes_delivery_error(monkeypatch, app_config):
    def boom(*a, **k):
        raise ConnectionRefusedError("no")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(DeliveryError):
        SmtpMailer(_config(app_config)).send("a@x.pl", "s", "b")


def test_gsmservice_payload(monkeypatch):
    seen = {}

    class FakeSend:
        def send(self, request):
            seen["request"] = request

    class FakeClient:
        def __init__(self, bearer):
            seen["bearer"] = bearer
            self.outgoing = type("O", (), {"sms": FakeSend()})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(notify, "Client", FakeClient)
    GsmServiceSms("tok", "Wishes").send("+48600100200", "hello")
    assert seen["bearer"] == "tok"
    assert seen["request"] == {
        "recipients": "+48600100200", "message": "hello", "sender": "Wishes",
        "type": 1, "unicode": True, "flash": False, "date_": None,
    }


def test_gsmservice_error_becomes_delivery_error(monkeypatch):
    def boom(bearer):
        raise RuntimeError("api")

    monkeypatch.setattr(notify, "Client", boom)
    with pytest.raises(DeliveryError):
        GsmServiceSms("tok", None).send("+48600100200", "x")


def test_share_sms_polish(app, db, user):
    wl = create_wishlist(db, user, name="Urodziny", description=None)
    link = "https://wish.example/s/abc"
    with app.app_context():
        en = share_sms(user, wl, link)
        user.language = "pl"
        pl = share_sms(user, wl, link)
    assert link in en and link in pl and en != pl
