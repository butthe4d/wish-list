from sqlalchemy import create_engine, text

from wishlist import bootstrap
from wishlist.keys import hash_key

from fakes import RecordingMailer


def _env(monkeypatch, tmp_path, **extra):
    env = {"BASE_URL": "https://wish.example", "SECRET_KEY": "s",
           "DATABASE_URL": f"sqlite:///{tmp_path / 'b.db'}", **extra}
    for k in ("ADMIN_EMAIL", "ADMIN_NAME", "SMTP_HOST", "DEFAULT_LANGUAGE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env["DATABASE_URL"]


def _users(url):
    with create_engine(url).connect() as c:
        return c.execute(text("select name, email, language, is_admin, key_hash from user")).all()


def test_bootstrap_creates_admin_once(monkeypatch, tmp_path, capsys):
    url = _env(monkeypatch, tmp_path, ADMIN_EMAIL="Boss@x.pl", DEFAULT_LANGUAGE="pl")
    assert bootstrap.main() == 0
    out = capsys.readouterr().out
    line = out.strip()
    assert line.startswith("Admin link: https://wish.example/u/") and "\n" not in line
    key = line.rsplit("/u/", 1)[1]
    [(name, email, lang, admin, key_hash)] = _users(url)
    assert (name, email, lang, admin, key_hash) == ("Boss", "boss@x.pl", "pl", 1, hash_key(key))
    assert bootstrap.main() == 0
    assert capsys.readouterr().out == ""
    assert len(_users(url)) == 1


def test_bootstrap_emails_link_when_mailer(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, ADMIN_EMAIL="boss@x.pl", ADMIN_NAME="Boss")
    mailer = RecordingMailer()
    assert bootstrap.main(mailer=mailer) == 0
    link = capsys.readouterr().out.strip().removeprefix("Admin link: ")
    assert mailer.sent[0][0] == "boss@x.pl" and link in mailer.sent[0][2]


def test_bootstrap_without_admin_email_warns(monkeypatch, tmp_path, capsys):
    url = _env(monkeypatch, tmp_path)
    assert bootstrap.main() == 0
    cap = capsys.readouterr()
    assert "ADMIN_EMAIL" in cap.err and cap.out == ""
    assert _users(url) == []


def test_bootstrap_invalid_config(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("SECRET_KEY")
    assert bootstrap.main() == 1
    assert "SECRET_KEY" in capsys.readouterr().err
