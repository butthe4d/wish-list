import pytest

from wishlist.config import Config, ConfigError

LONG = "k" * 40


def test_requires_base_url_and_secret():
    with pytest.raises(ConfigError) as e:
        Config.from_env({})
    assert "BASE_URL" in str(e.value) and "SECRET_KEY" in str(e.value)


def test_defaults_and_trailing_slash():
    c = Config.from_env({"BASE_URL": "https://w.example/", "SECRET_KEY": LONG})
    assert c.base_url == "https://w.example"
    assert c.database_url == "sqlite:////data/wishlist.db"
    assert c.default_language == "en"
    assert not c.email_enabled and not c.sms_enabled


def test_smtp_from_defaults_to_user_and_port_parsed():
    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG, "SMTP_HOST": "smtp.gmail.com",
                         "SMTP_PORT": "465", "SMTP_USER": "me@gmail.com", "SMTP_SECURITY": "ssl"})
    assert c.email_enabled and c.smtp_port == 465 and c.smtp_from == "me@gmail.com"


@pytest.mark.parametrize("key,val", [("SMTP_SECURITY", "tls"), ("DEFAULT_LANGUAGE", "de"), ("SMTP_PORT", "abc")])
def test_rejects_invalid_values(key, val):
    with pytest.raises(ConfigError):
        Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG, key: val})


@pytest.mark.parametrize("secret", ["s", "k" * 31, "change-me", "changeme", "secret", "dev", "CHANGE-ME"])
def test_rejects_weak_secret_key(secret):
    with pytest.raises(ConfigError) as e:
        Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": secret})
    assert "token_urlsafe" in str(e.value)


def test_accepts_32_char_secret_key():
    assert Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": "k" * 32}).secret_key == "k" * 32


def test_fetch_deny_cidrs_default_empty():
    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG})
    assert c.fetch_deny_cidrs == () and c.fetch_deny_networks == ()


def test_fetch_deny_cidrs_parsed_comma_and_space_separated():
    import ipaddress

    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG,
                         "FETCH_DENY_CIDRS": " 203.0.113.7/32, 2001:db8:1:2::/64  198.51.100.9,,"})
    assert c.fetch_deny_cidrs == ("203.0.113.7/32", "2001:db8:1:2::/64", "198.51.100.9/32")
    assert c.fetch_deny_networks == (
        ipaddress.ip_network("203.0.113.7/32"),
        ipaddress.ip_network("2001:db8:1:2::/64"),
        ipaddress.ip_network("198.51.100.9/32"),
    )


def test_fetch_deny_cidrs_non_strict_host_bits():
    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG,
                         "FETCH_DENY_CIDRS": "2001:db8:1:2::5/64"})
    assert c.fetch_deny_cidrs == ("2001:db8:1:2::/64",)


@pytest.mark.parametrize("val", ["garbage", "10.0.0.0/33", "1.2.3.4/abc", "2001:db8::/129"])
def test_fetch_deny_cidrs_rejects_garbage(val):
    with pytest.raises(ConfigError) as e:
        Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": LONG, "FETCH_DENY_CIDRS": val})
    assert "FETCH_DENY_CIDRS" in str(e.value)
