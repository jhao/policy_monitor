from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

from flask import current_app


class EmailConfigError(RuntimeError):
    pass


def _get_setting(name: str, default: str | None = None) -> str:
    value = current_app.config.get(name) if current_app else os.getenv(name)
    if value:
        return value
    if default is not None:
        return default
    raise EmailConfigError(f"Missing email configuration: {name}")


def send_email(subject: str, recipients: Iterable[str], html_body: str, text_body: str | None = None) -> None:
    host = _get_setting("SMTP_HOST")
    port = int(_get_setting("SMTP_PORT", "587"))
    username = _get_setting("SMTP_USERNAME")
    password = _get_setting("SMTP_PASSWORD")
    use_tls = _get_setting("SMTP_USE_TLS", "true").lower() != "false"
    sender = _get_setting("SMTP_SENDER", username)

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    if text_body is None:
        text_body = html_body

    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.sendmail(sender, recipients, message.as_string())
