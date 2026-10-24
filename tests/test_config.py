import pytest

from wishlist.config import Config, ConfigError


def test_requires_base_url_and_secret():
    with pytest.raises(ConfigError) as e:
        Config.from_env({})
    assert "BASE_URL" in str(e.value) and "SECRET_KEY" in str(e.value)


def test_defaults_and_trailing_slash():
    c = Config.from_env({"BASE_URL": "https://w.example/", "SECRET_KEY": "s"})
    assert c.base_url == "https://w.example"
    assert c.database_url == "sqlite:////data/wishlist.db"
    assert c.default_language == "en"
    assert not c.email_enabled and not c.sms_enabled


def test_smtp_from_defaults_to_user_and_port_parsed():
    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": "s", "SMTP_HOST": "smtp.gmail.com",
                         "SMTP_PORT": "465", "SMTP_USER": "me@gmail.com", "SMTP_SECURITY": "ssl"})
    assert c.email_enabled and c.smtp_port == 465 and c.smtp_from == "me@gmail.com"


@pytest.mark.parametrize("key,val", [("SMTP_SECURITY", "tls"), ("DEFAULT_LANGUAGE", "de"), ("SMTP_PORT", "abc")])
def test_rejects_invalid_values(key, val):
    with pytest.raises(ConfigError):
        Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": "s", key: val})
