from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass, replace
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Iterable

import requests
from flask import current_app
from ssl import create_default_context

from database import SessionLocal
from models import NotificationSetting


LOGGER = logging.getLogger(__name__)


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
    use_ssl: bool = False


def _get_setting(name: str, default: str | None = None) -> str:
    value = current_app.config.get(name) if current_app else os.getenv(name)
    if value:
        return value
    if default is not None:
        return default
    raise NotificationConfigError(f"Missing configuration: {name}")


def _get_optional_setting(name: str) -> str | None:
    value = current_app.config.get(name) if current_app else os.getenv(name)
    return value or None


def _resolve_transport_options(
    port: int, *, encryption_enabled: bool, ssl_override: bool | None
) -> tuple[bool, bool]:
    """Determine whether to use STARTTLS or implicit SSL for the SMTP connection."""

    if not encryption_enabled:
        return False, False

    if ssl_override is not None:
        if ssl_override:
            return False, True
        return True, False

    if port == 465:
        return False, True

    return True, False


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
        encryption_enabled = setting.smtp_use_tls
        if encryption_enabled is None:
            encryption_enabled = True
        port_value = setting.smtp_port or 587
        if not encryption_enabled and port_value == 465:
            # 部分历史数据未设置 TLS，但端口为 465 时需要使用 SSL
            encryption_enabled = True
        use_tls, use_ssl = _resolve_transport_options(
            port_value,
            encryption_enabled=bool(encryption_enabled),
            ssl_override=None,
        )
        return EmailSettings(
            host=setting.smtp_host,
            port=port_value,
            username=setting.smtp_username,
            password=setting.smtp_password,
            use_tls=use_tls,
            sender=sender,
            use_ssl=use_ssl,
        )

    host = _get_setting("SMTP_HOST")
    port = int(_get_setting("SMTP_PORT", "587"))
    username = _get_setting("SMTP_USERNAME")
    password = _get_setting("SMTP_PASSWORD")
    use_tls_flag = _get_setting("SMTP_USE_TLS", "true").lower() != "false"
    use_ssl_override_raw = _get_optional_setting("SMTP_USE_SSL")
    use_ssl_override: bool | None
    if use_ssl_override_raw is None:
        use_ssl_override = None
    else:
        use_ssl_override = use_ssl_override_raw.lower() not in {"false", "0", "no"}
    use_tls, use_ssl = _resolve_transport_options(
        port,
        encryption_enabled=use_tls_flag,
        ssl_override=use_ssl_override,
    )
    sender = _get_setting("SMTP_SENDER", username)
    return EmailSettings(
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        sender=sender,
        use_ssl=use_ssl,
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

    recipient_list = list(recipients)
    if not recipient_list:
        raise NotificationConfigError("未提供收件人，无法发送邮件")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(recipient_list)

    if text_body is None:
        text_body = html_body

    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    LOGGER.info(
        "Sending email via %s:%s as %s to %s (ssl=%s, starttls=%s)",
        settings.host,
        settings.port,
        settings.sender,
        ", ".join(recipient_list),
        settings.use_ssl,
        settings.use_tls,
    )
    _deliver_email(settings, recipient_list, message)
    LOGGER.info("Email sent successfully: %s", subject)


def _deliver_email(
    settings: EmailSettings,
    recipient_list: list[str],
    message: MIMEMultipart,
) -> None:
    smtp_client_cls = smtplib.SMTP_SSL if settings.use_ssl else smtplib.SMTP
    smtp_kwargs: dict[str, Any] = {"timeout": 30}
    if settings.use_ssl:
        smtp_kwargs["context"] = create_default_context()

    smtp_disconnected_exc = getattr(
        smtplib,
        "SMTPServerDisconnected",
        getattr(smtplib, "SMTPException", Exception),
    )

    try:
        try:
            smtp_connection = smtp_client_cls(settings.host, settings.port, **smtp_kwargs)
        except TypeError:
            LOGGER.debug(
                "SMTP client %s does not accept optional kwargs, retrying without",
                smtp_client_cls,
            )
            smtp_connection = smtp_client_cls(settings.host, settings.port)

        with smtp_connection as server:
            server.ehlo()
            if settings.use_tls:
                try:
                    server.starttls(context=create_default_context())
                except TypeError:
                    LOGGER.debug(
                        "SMTP server.starttls does not accept context argument, retrying without",
                    )
                    server.starttls()
                server.ehlo()
            server.login(settings.username, settings.password)
            server.sendmail(settings.sender, recipient_list, message.as_string())
    except smtp_disconnected_exc as exc:
        LOGGER.warning(
            "SMTP connection closed unexpectedly when using ssl=%s starttls=%s: %s",
            settings.use_ssl,
            settings.use_tls,
            exc,
        )
        if settings.use_ssl:
            raise
        fallback_settings = replace(settings, use_ssl=True, use_tls=False)
        LOGGER.info(
            "Retrying email delivery with implicit SSL on %s:%s",
            fallback_settings.host,
            fallback_settings.port,
        )
        fallback_kwargs: dict[str, Any] = {"timeout": 30, "context": create_default_context()}
        try:
            fallback_connection = smtplib.SMTP_SSL(
                fallback_settings.host,
                fallback_settings.port,
                **fallback_kwargs,
            )
        except TypeError:
            LOGGER.debug(
                "SMTP_SSL does not accept optional kwargs, retrying without extra parameters",
            )
            fallback_connection = smtplib.SMTP_SSL(
                fallback_settings.host,
                fallback_settings.port,
            )

        with fallback_connection as server:
            server.ehlo()
            server.login(fallback_settings.username, fallback_settings.password)
            server.sendmail(
                fallback_settings.sender,
                recipient_list,
                message.as_string(),
            )
def send_dingtalk_message(payload: dict[str, Any]) -> str:
    webhook_url = _get_dingtalk_webhook()
    LOGGER.info("Sending DingTalk message to %s", webhook_url)
    response = requests.post(webhook_url, json=payload, timeout=10)
    response.raise_for_status()
    LOGGER.info("DingTalk message sent successfully")
    return webhook_url
