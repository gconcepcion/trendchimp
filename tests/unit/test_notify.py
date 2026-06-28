from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from trendchimp.config.settings import AlertSettings
from trendchimp.notify import EmailNotifier, NullNotifier, build_notifier


def _email_settings(**over):
    base = dict(
        enabled=True, smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="user", smtp_password="pw", use_tls=True,
        email_from="bot@example.com", email_to="me@example.com, you@example.com",
    )
    base.update(over)
    return AlertSettings(**base)


def test_email_notifier_sends_via_smtp():
    with patch("trendchimp.notify.smtplib.SMTP") as smtp:
        session = smtp.return_value.__enter__.return_value
        notifier = EmailNotifier(
            host="smtp.example.com", port=587, username="user", password="pw",
            use_tls=True, sender="bot@example.com",
            recipients=["me@example.com", "you@example.com"],
        )
        notifier.alert(subject="UNPROTECTED AAPL", body="place a stop")

    smtp.assert_called_once()
    session.starttls.assert_called_once()
    session.login.assert_called_once_with("user", "pw")
    session.send_message.assert_called_once()
    msg = session.send_message.call_args.args[0]
    assert msg["Subject"] == "UNPROTECTED AAPL"
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "me@example.com, you@example.com"


def test_email_notifier_never_raises(caplog):
    with patch("trendchimp.notify.smtplib.SMTP", side_effect=OSError("no network")):
        notifier = EmailNotifier(
            host="smtp.example.com", port=587, username="", password="",
            use_tls=False, sender="bot@example.com", recipients=["me@example.com"],
        )
        with caplog.at_level(logging.ERROR):
            notifier.alert(subject="x", body="y")  # must not raise
    assert any("alert" in r.message.lower() for r in caplog.records)


def test_null_notifier_is_noop():
    NullNotifier().alert(subject="x", body="y")  # no exception, no side effects


def test_build_notifier_null_when_disabled():
    assert isinstance(build_notifier(_email_settings(enabled=False)), NullNotifier)


def test_build_notifier_null_when_unconfigured():
    assert isinstance(build_notifier(_email_settings(smtp_host="")), NullNotifier)
    assert isinstance(build_notifier(_email_settings(email_to="")), NullNotifier)


def test_build_notifier_email_when_configured():
    notifier = build_notifier(_email_settings())
    assert isinstance(notifier, EmailNotifier)
    assert notifier.recipients == ["me@example.com", "you@example.com"]
