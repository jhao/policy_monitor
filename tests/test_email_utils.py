from types import SimpleNamespace

import email_utils
from email_utils import EmailSettings, _resolve_transport_options, send_email


def test_resolve_transport_options_uses_ssl_for_port_465():
    use_tls, use_ssl = _resolve_transport_options(465, encryption_enabled=True, ssl_override=None)
    assert not use_tls
    assert use_ssl


def test_resolve_transport_options_respects_override():
    use_tls, use_ssl = _resolve_transport_options(465, encryption_enabled=True, ssl_override=False)
    assert use_tls
    assert not use_ssl


def test_resolve_transport_options_disable_encryption():
    use_tls, use_ssl = _resolve_transport_options(25, encryption_enabled=False, ssl_override=None)
    assert not use_tls
    assert not use_ssl


def test_resolve_transport_options_explicit_ssl_override():
    use_tls, use_ssl = _resolve_transport_options(587, encryption_enabled=True, ssl_override=True)
    assert not use_tls
    assert use_ssl


class _DummyServer:
    def __init__(self, tracker: dict[str, object], label: str) -> None:
        self._tracker = tracker
        tracker["client"] = label

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        self._tracker.setdefault("ehlo", 0)
        self._tracker["ehlo"] = int(self._tracker["ehlo"]) + 1

    def starttls(self):
        self._tracker.setdefault("starttls", 0)
        self._tracker["starttls"] = int(self._tracker["starttls"]) + 1

    def login(self, username: str, password: str):
        self._tracker["login"] = (username, password)

    def sendmail(self, sender: str, recipients, message: str):
        self._tracker["sendmail"] = {
            "sender": sender,
            "recipients": list(recipients),
            "message_length": len(message),
        }


def test_send_email_uses_smtp_ssl(monkeypatch):
    tracker: dict[str, object] = {}

    class DummySSL(_DummyServer):
        def __init__(self, host: str, port: int):
            super().__init__(tracker, "ssl")
            tracker["host"] = host
            tracker["port"] = port

    class DummySMTP(_DummyServer):
        def __init__(self, host: str, port: int):
            raise AssertionError("SMTP should not be used when SSL is enabled")

    monkeypatch.setattr(
        email_utils,
        "smtplib",
        SimpleNamespace(SMTP=DummySMTP, SMTP_SSL=DummySSL),
    )
    monkeypatch.setattr(
        email_utils,
        "_load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com",
            port=465,
            username="user",
            password="secret",
            use_tls=False,
            sender="no-reply@example.com",
            use_ssl=True,
        ),
    )

    send_email("Subject", ["alice@example.com"], "<p>Hello</p>", "Hello")

    assert tracker["client"] == "ssl"
    assert tracker["host"] == "smtp.example.com"
    assert tracker["port"] == 465
    assert "starttls" not in tracker
    assert tracker["login"] == ("user", "secret")
    assert tracker["sendmail"]["sender"] == "no-reply@example.com"
    assert tracker["sendmail"]["recipients"] == ["alice@example.com"]


def test_send_email_uses_starttls(monkeypatch):
    tracker: dict[str, object] = {}

    class DummySMTP(_DummyServer):
        def __init__(self, host: str, port: int):
            super().__init__(tracker, "smtp")
            tracker["host"] = host
            tracker["port"] = port

    class DummySSL(_DummyServer):
        def __init__(self, host: str, port: int):
            raise AssertionError("SMTP_SSL should not be used when only STARTTLS is required")

    monkeypatch.setattr(
        email_utils,
        "smtplib",
        SimpleNamespace(SMTP=DummySMTP, SMTP_SSL=DummySSL),
    )
    monkeypatch.setattr(
        email_utils,
        "_load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            sender="no-reply@example.com",
            use_ssl=False,
        ),
    )

    send_email("Subject", ["alice@example.com"], "<p>Hello</p>", "Hello")

    assert tracker["client"] == "smtp"
    assert tracker["host"] == "smtp.example.com"
    assert tracker["port"] == 587
    assert tracker["starttls"] == 1
    assert tracker["login"] == ("user", "secret")
    assert tracker["sendmail"]["sender"] == "no-reply@example.com"
    assert tracker["sendmail"]["recipients"] == ["alice@example.com"]
