from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

import requests
from flask import current_app

from database import SessionLocal
from models import NotificationSetting


class NotificationConfigError(RuntimeError):
    pass


@dataclass
class EmailSettings:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    sender: str


def _get_setting(name: str, default: str | None = None) -> str:
    value = current_app.config.get(name) if current_app else os.getenv(name)
    if value:
        return value
    if default is not None:
        return default
    raise NotificationConfigError(f"Missing configuration: {name}")


def _load_email_settings() -> EmailSettings:
    session = SessionLocal()
    try:
        setting = (
            session.query(NotificationSetting)
            .filter(NotificationSetting.channel == "email")
            .one_or_none()
        )
    finally:
        session.close()

    if setting and setting.smtp_host and setting.smtp_username and setting.smtp_password:
        sender = setting.smtp_sender or setting.smtp_username
        return EmailSettings(
            host=setting.smtp_host,
            port=setting.smtp_port or 587,
            username=setting.smtp_username,
            password=setting.smtp_password,
            use_tls=bool(setting.smtp_use_tls),
            sender=sender,
        )

    host = _get_setting("SMTP_HOST")
    port = int(_get_setting("SMTP_PORT", "587"))
    username = _get_setting("SMTP_USERNAME")
    password = _get_setting("SMTP_PASSWORD")
    use_tls = _get_setting("SMTP_USE_TLS", "true").lower() != "false"
    sender = _get_setting("SMTP_SENDER", username)
    return EmailSettings(
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        sender=sender,
    )


def _get_dingtalk_webhook() -> str:
    session = SessionLocal()
    try:
        setting = (
            session.query(NotificationSetting)
            .filter(NotificationSetting.channel == "dingtalk")
            .one_or_none()
        )
    finally:
        session.close()

    if setting and setting.webhook_url:
        return setting.webhook_url
    value = os.getenv("DINGTALK_WEBHOOK")
    if value:
        return value
    raise NotificationConfigError("Missing DingTalk webhook configuration")


def send_email(
    subject: str,
    recipients: Iterable[str],
    html_body: str,
    text_body: str | None = None,
) -> None:
    settings = _load_email_settings()

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(recipients)

    if text_body is None:
        text_body = html_body

    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(settings.host, settings.port) as server:
        if settings.use_tls:
            server.starttls()
        server.login(settings.username, settings.password)
        server.sendmail(settings.sender, recipients, message.as_string())


def send_dingtalk_message(title: str, content: str, url: str | None = None) -> None:
    webhook_url = _get_dingtalk_webhook()
    payload = {
        "title": title,
        "content": content,
        "url": url or "",
    }
    response = requests.post(webhook_url, json=payload, timeout=10)
    response.raise_for_status()
