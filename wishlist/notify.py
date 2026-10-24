from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from flask import current_app
from gsmservice_gateway import Client

from .config import Config

log = logging.getLogger(__name__)

# Pass to create_app(mailer=..., sms=...) to disable a channel even when config would enable it.
DISABLED = object()

TIMEOUT = 15


class DeliveryError(Exception):
    pass


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmsSender(Protocol):
    def send(self, to: str, text: str) -> None: ...


class SmtpMailer:
    def __init__(self, config: Config):
        self._c = config

    def send(self, to: str, subject: str, body: str) -> None:
        c = self._c
        msg = EmailMessage()
        msg["From"] = c.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body, charset="utf-8")
        try:
            factory = smtplib.SMTP_SSL if c.smtp_security == "ssl" else smtplib.SMTP
            with factory(c.smtp_host, c.smtp_port, timeout=TIMEOUT) as smtp:
                if c.smtp_security == "starttls":
                    smtp.starttls()
                if c.smtp_user:
                    smtp.login(c.smtp_user, c.smtp_password or "")
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise DeliveryError(f"email delivery failed: {e}") from e


class GsmServiceSms:
    def __init__(self, token: str, sender: str | None):
        self._token = token
        self._sender = sender

    def send(self, to: str, text: str) -> None:
        try:
            with Client(bearer=self._token) as client:
                client.outgoing.sms.send(
                    request={
                        "recipients": to,
                        "message": text,
                        "sender": self._sender,
                        "type": 1,
                        "unicode": True,
                        "flash": False,
                        "date_": None,
                    }
                )
        except Exception as e:
            raise DeliveryError(f"sms delivery failed: {type(e).__name__}") from e


def build_mailer(config: Config, injected) -> Mailer | None:
    if injected is DISABLED:
        return None
    if injected is not None:
        return injected
    return SmtpMailer(config) if config.email_enabled else None


def build_sms(config: Config, injected) -> SmsSender | None:
    if injected is DISABLED:
        return None
    if injected is not None:
        return injected
    if config.sms_enabled:
        return GsmServiceSms(config.gsmservice_token, config.gsmservice_sender)
    return None


def get_mailer() -> Mailer | None:
    return current_app.extensions["mailer"]


def get_sms() -> SmsSender | None:
    return current_app.extensions["sms"]
