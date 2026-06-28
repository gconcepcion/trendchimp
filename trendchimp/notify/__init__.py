"""Best-effort external alerting for safety-critical events.

A notifier is invoked when a position becomes unprotected or the kill-switch trips,
so the operator can respond even when the bot is running headless. Sending is always
best-effort: a delivery failure is logged but never propagates into the trading path.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from trendchimp.config.settings import AlertSettings

logger = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    def alert(self, subject: str, body: str) -> None: ...


class NullNotifier:
    """No-op notifier used when alerting is disabled or unconfigured."""

    def alert(self, subject: str, body: str) -> None:  # noqa: D401
        return None


class EmailNotifier:
    """Sends a plain-text email per alert via SMTP. Never raises."""

    def __init__(
        self, *, host: str, port: int, username: str, password: str,
        use_tls: bool, sender: str, recipients: list[str],
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.sender = sender
        self.recipients = recipients

    def alert(self, subject: str, body: str) -> None:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.recipients)
            msg.set_content(body)
            with smtplib.SMTP(self.host, self.port, timeout=10) as session:
                if self.use_tls:
                    session.starttls()
                if self.username:
                    session.login(self.username, self.password)
                session.send_message(msg)
            logger.info("Alert email sent: %s", subject)
        except Exception:
            # Alerting must never break trading; just log the failure loudly.
            logger.exception("Alert email FAILED to send (%s)", subject)


def build_notifier(alerts: "AlertSettings") -> Notifier:
    """Construct a notifier from settings, or a NullNotifier if not fully configured."""
    recipients = [r.strip() for r in alerts.email_to.split(",") if r.strip()]
    if not alerts.enabled or not alerts.smtp_host or not recipients:
        return NullNotifier()
    return EmailNotifier(
        host=alerts.smtp_host, port=alerts.smtp_port,
        username=alerts.smtp_username, password=alerts.smtp_password,
        use_tls=alerts.use_tls, sender=alerts.email_from or alerts.smtp_username,
        recipients=recipients,
    )
