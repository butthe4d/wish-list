import pytest
from conftest import login, make_user
from fakes import FailingMailer, FailingSms

from wishlist import create_app
from wishlist.catalog import create_wishlist
from wishlist.db import upgrade_db
from wishlist.errors import ValidationError
from wishlist.keys import share_key
from wishlist.models import ShareLog, utcnow
from datetime import timedelta
from wishlist.notify import DISABLED
from wishlist.sharing import normalize_phone, parse_emails


@pytest.mark.parametrize(
    "raw,expected",
    [("a@x.pl, b@y.com", ["a@x.pl", "b@y.com"]), ("a@x.pl;a@x.pl", ["a@x.pl"]),
     ("a@x.pl b@y.com\nc@z.org", ["a@x.pl", "b@y.com", "c@z.org"])],
)
def test_parse_emails(raw, expected):
    assert parse_emails(raw) == expected


@pytest.mark.parametrize("raw", ["a@x.pl, nope", "", "  ", ",".join(f"a{i}@x.pl" for i in range(11))])
def test_parse_emails_rejects_bad(raw):
    with pytest.raises(ValidationError):
        parse_emails(raw)


@pytest.mark.parametrize("raw,expected", [("+48 600-100-200", "+48600100200"), ("+1 (555) 123-4567", "+15551234567")])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["600100200", "+12", "+48 abc", ""])
def test_normalize_phone_rejects(raw):
    with pytest.raises(ValidationError) as e:
        normalize_phone(raw)
    assert "+48" in e.value.message


def _owner_list(client, db):
    u, key = make_user(db, "Ania", "a@x.pl")
    login(client, key)
    return u, create_wishlist(db, u, name="Birthday", description=None)


def test_share_email_sends_link_and_logs(client, db, mailer):
    u, wl = _owner_list(client, db)
    r = client.post(f"/lists/{wl.id}/share/email", data={"emails": "b@y.com, c@z.org"})
    assert r.status_code == 302
    assert [m[0] for m in mailer.sent] == ["b@y.com", "c@z.org"]
    link = "https://wish.example/s/" + share_key("test-secret", wl.id, 1)
    assert all(link in body for _, _, body in mailer.sent)
    logs = db.query(ShareLog).order_by(ShareLog.id).all()
    assert [(l.channel, l.recipient) for l in logs] == [("email", "b@y.com"), ("email", "c@z.org")]
    page = client.get(f"/lists/{wl.id}").text
    assert "b@y.com" in page and "c@z.org" in page


def test_share_email_invalid_shows_error(client, db, mailer):
    _, wl = _owner_list(client, db)
    r = client.post(f"/lists/{wl.id}/share/email", data={"emails": "nope"}, follow_redirects=True)
    assert "valid email" in r.text.lower()
    assert mailer.sent == [] and db.query(ShareLog).count() == 0


def test_share_email_partial_failure(client, db, app):
    class Half:
        def send(self, to, subject, body):
            from wishlist.notify import DeliveryError

            if to.startswith("bad"):
                raise DeliveryError("x")

    app.extensions["mailer"] = Half()
    _, wl = _owner_list(client, db)
    r = client.post(
        f"/lists/{wl.id}/share/email", data={"emails": "ok@y.com, bad@y.com"}, follow_redirects=True
    )
    assert "Couldn&#39;t send to bad@y.com" in r.text
    assert [l.recipient for l in db.query(ShareLog)] == ["ok@y.com"]


def test_share_email_failure_shows_message(client, db, app):
    app.extensions["mailer"] = FailingMailer()
    _, wl = _owner_list(client, db)
    r = client.post(f"/lists/{wl.id}/share/email", data={"emails": "b@y.com"}, follow_redirects=True)
    assert "Couldn&#39;t send" in r.text
    assert db.query(ShareLog).count() == 0


def test_share_sms_sends_and_logs(client, db, sms):
    _, wl = _owner_list(client, db)
    r = client.post(f"/lists/{wl.id}/share/sms", data={"phone": "+48 600-100-200"})
    assert r.status_code == 302
    assert sms.sent[0][0] == "+48600100200"
    assert "https://wish.example/s/" in sms.sent[0][1]
    log = db.query(ShareLog).one()
    assert (log.channel, log.recipient) == ("sms", "+48600100200")


def test_share_sms_bad_phone_and_failure(client, db, app):
    _, wl = _owner_list(client, db)
    r = client.post(f"/lists/{wl.id}/share/sms", data={"phone": "600"}, follow_redirects=True)
    assert "international format" in r.text
    app.extensions["sms"] = FailingSms()
    r = client.post(f"/lists/{wl.id}/share/sms", data={"phone": "+48600100200"}, follow_redirects=True)
    assert "Couldn&#39;t send" in r.text
    assert db.query(ShareLog).count() == 0


def test_share_routes_only_for_owner(client, db, mailer):
    other, _ = make_user(db, "Basia", "b@x.pl")
    wl = create_wishlist(db, other, name="X", description=None)
    _, key = make_user(db, "Cela", "c@x.pl")
    login(client, key)
    assert client.post(f"/lists/{wl.id}/share/email", data={"emails": "a@x.pl"}).status_code == 404
    assert client.post(f"/lists/{wl.id}/share/regenerate").status_code == 404
    assert mailer.sent == []


def test_regenerate_changes_link_and_redirects(client, db):
    _, wl = _owner_list(client, db)
    old = share_key("test-secret", wl.id, 1)
    r = client.post(f"/lists/{wl.id}/share/regenerate")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/lists/{wl.id}")
    page = client.get(f"/lists/{wl.id}").text
    assert share_key("test-secret", wl.id, 2) in page and old not in page
    assert "Create new share link (old one stops working)" in page


def test_sms_form_hidden_and_route_404_when_disabled(app_config, mailer):
    upgrade_db(app_config.database_url)
    app = create_app(app_config, mailer=mailer, sms=DISABLED)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = app.test_client()
    from sqlalchemy.orm import sessionmaker

    db = sessionmaker(bind=app.extensions["wishlist_engine"])()
    _, wl = _owner_list(client, db)
    assert app.extensions["sms"] is None
    assert 'name="phone"' not in client.get(f"/lists/{wl.id}").text
    assert 'name="emails"' in client.get(f"/lists/{wl.id}").text
    assert client.post(f"/lists/{wl.id}/share/sms", data={"phone": "+48600100200"}).status_code == 404


def test_email_form_hidden_and_route_404_when_disabled(app_config, sms):
    upgrade_db(app_config.database_url)
    app = create_app(app_config, mailer=DISABLED, sms=sms)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = app.test_client()
    from sqlalchemy.orm import sessionmaker

    db = sessionmaker(bind=app.extensions["wishlist_engine"])()
    _, wl = _owner_list(client, db)
    assert 'name="emails"' not in client.get(f"/lists/{wl.id}").text
    assert client.post(f"/lists/{wl.id}/share/email", data={"emails": "a@x.pl"}).status_code == 404


def test_share_email_newline_in_list_name_single_line_subject(client, db, mailer):
    u, key = make_user(db, "Ania", "a@x.pl")
    login(client, key)
    wl = create_wishlist(db, u, name="Bday\nBcc: evil@x.pl", description=None)
    r = client.post(f"/lists/{wl.id}/share/email", data={"emails": "b@y.com"})
    assert r.status_code == 302
    (to, subject, _), = mailer.sent
    assert "\n" not in subject and "\r" not in subject


def _fill_log(db, wl, channel, n, age=timedelta(hours=1)):
    for i in range(n):
        db.add(ShareLog(wishlist_id=wl.id, channel=channel, recipient=f"r{i}", sent_at=utcnow() - age))
    db.commit()


def test_sms_daily_limit(client, db, sms):
    _, wl = _owner_list(client, db)
    _fill_log(db, wl, "sms", 19)
    _fill_log(db, wl, "sms", 5, age=timedelta(hours=25))  # old entries don't count
    assert client.post(f"/lists/{wl.id}/share/sms", data={"phone": "+48600100200"}).status_code == 302
    assert len(sms.sent) == 1
    r = client.post(f"/lists/{wl.id}/share/sms", data={"phone": "+48600100201"}, follow_redirects=True)
    assert "Daily sending limit reached, try again tomorrow." in r.text
    assert len(sms.sent) == 1 and db.query(ShareLog).filter_by(channel="sms").count() == 25


def test_email_batch_over_quota_refused(client, db, mailer):
    _, wl = _owner_list(client, db)
    _fill_log(db, wl, "email", 49)
    r = client.post(f"/lists/{wl.id}/share/email", data={"emails": "a@y.com, b@y.com"}, follow_redirects=True)
    assert "Daily sending limit reached" in r.text
    assert mailer.sent == [] and db.query(ShareLog).count() == 49
    client.post(f"/lists/{wl.id}/share/email", data={"emails": "a@y.com"})
    assert len(mailer.sent) == 1
