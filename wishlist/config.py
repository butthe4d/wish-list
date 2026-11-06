from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from flask import current_app

LANGUAGE_NAMES: dict[str, str] = {"en": "English", "pl": "Polski"}
SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_NAMES)
SMTP_SECURITY_MODES = ("starttls", "ssl", "none")
MIN_SECRET_KEY_LENGTH = 32


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str
    secret_key: str
    database_url: str = "sqlite:////data/wishlist.db"
    admin_name: str | None = None
    admin_email: str | None = None
    default_language: str = "en"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: str = "starttls"
    gsmservice_token: str | None = None
    gsmservice_sender: str | None = None
    data_dir: str = "/data"

    @property
    def images_dir(self) -> str:
        return f"{self.data_dir}/images"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def sms_enabled(self) -> bool:
        return bool(self.gsmservice_token)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        def get(name: str) -> str | None:
            return env.get(name) or None

        missing = [n for n in ("BASE_URL", "SECRET_KEY") if not get(n)]
        if missing:
            raise ConfigError("Missing required environment variables: " + ", ".join(missing))

        secret = get("SECRET_KEY")
        if len(secret) < MIN_SECRET_KEY_LENGTH:
            raise ConfigError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

        language = get("DEFAULT_LANGUAGE") or "en"
        if language not in SUPPORTED_LANGUAGES:
            raise ConfigError(
                f"DEFAULT_LANGUAGE must be one of {', '.join(SUPPORTED_LANGUAGES)}, got {language!r}"
            )
        security = get("SMTP_SECURITY") or "starttls"
        if security not in SMTP_SECURITY_MODES:
            raise ConfigError(
                f"SMTP_SECURITY must be one of {', '.join(SMTP_SECURITY_MODES)}, got {security!r}"
            )
        port_raw = get("SMTP_PORT") or "587"
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigError(f"SMTP_PORT must be an integer, got {port_raw!r}") from None

        smtp_user = get("SMTP_USER")
        return cls(
            base_url=get("BASE_URL").rstrip("/"),
            secret_key=get("SECRET_KEY"),
            database_url=get("DATABASE_URL") or "sqlite:////data/wishlist.db",
            admin_name=get("ADMIN_NAME"),
            admin_email=get("ADMIN_EMAIL"),
            default_language=language,
            smtp_host=get("SMTP_HOST"),
            smtp_port=port,
            smtp_user=smtp_user,
            smtp_password=get("SMTP_PASSWORD"),
            smtp_from=get("SMTP_FROM") or smtp_user,
            smtp_security=security,
            gsmservice_token=get("GSMSERVICE_TOKEN"),
            gsmservice_sender=get("GSMSERVICE_SENDER"),
            data_dir=(get("DATA_DIR") or "/data").rstrip("/") or "/",
        )


def get_base_url() -> str:
    """BASE_URL of the running app (needs an app context)."""
    return current_app.config["WISHLIST"].base_url
