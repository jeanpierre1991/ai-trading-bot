"""Email alert notifier via SMTP (Milestone 14.2).

Uses an injectable SMTP transport so tests can mock delivery without network.
Incomplete SMTP configuration fails closed (returns False) without raising.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from alerts.notifier import Alert, AlertNotifier

_logger = logging.getLogger("trading_bot.alerts.email")


class SmtpTransport(Protocol):
    """Injectable SMTP port for EmailNotifier."""

    def send(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        message: EmailMessage,
        timeout: float = 10.0,
    ) -> None:
        """Deliver ``message``. May raise on transport failure."""


class StdlibSmtpTransport:
    """Default SMTP transport using ``smtplib`` (stdlib only)."""

    def send(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        message: EmailMessage,
        timeout: float = 10.0,
    ) -> None:
        with smtplib.SMTP(host=host, port=port, timeout=timeout) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)


class EmailNotifier(AlertNotifier):
    """Send alert emails through a configured SMTP endpoint."""

    def __init__(
        self,
        *,
        to_address: str,
        from_address: str,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        use_tls: bool = True,
        transport: SmtpTransport | None = None,
        timeout_seconds: float = 10.0,
        min_level: str | None = None,
    ) -> None:
        self._to_address = str(to_address or "").strip()
        self._from_address = str(from_address or "").strip()
        self._smtp_host = str(smtp_host or "").strip()
        self._smtp_port = int(smtp_port)
        self._smtp_username = str(smtp_username or "")
        self._smtp_password = str(smtp_password or "")
        self._use_tls = bool(use_tls)
        self._transport: SmtpTransport = (
            transport if transport is not None else StdlibSmtpTransport()
        )
        self._timeout_seconds = float(timeout_seconds)
        self._min_level = (min_level or "").strip().lower() or None
        self._sent_count = 0
        self._failure_count = 0

    @property
    def to_address(self) -> str:
        return self._to_address

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def configured(self) -> bool:
        return bool(self._to_address and self._from_address and self._smtp_host)

    def send(self, alert: Alert) -> bool:
        if not self.configured():
            self._failure_count += 1
            _logger.warning("email_notifier_skipped reason=incomplete_smtp_config")
            return False
        if self._min_level and not _level_at_least(alert.level.value, self._min_level):
            return False

        message = EmailMessage()
        message["Subject"] = f"[{alert.level.value.upper()}] {alert.title}"
        message["From"] = self._from_address
        message["To"] = self._to_address
        message.set_content(
            "\n".join(
                [
                    f"level={alert.level.value}",
                    f"source={alert.source}",
                    f"timestamp={alert.timestamp.isoformat()}",
                    f"title={alert.title}",
                    "",
                    alert.message,
                ]
            )
        )
        try:
            self._transport.send(
                host=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_username,
                password=self._smtp_password,
                use_tls=self._use_tls,
                message=message,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - never raise into callers
            self._failure_count += 1
            _logger.warning(
                "email_notifier_failed title=%s error=%s",
                alert.title,
                exc,
            )
            return False
        self._sent_count += 1
        return True


def _level_at_least(level: str, minimum: str) -> bool:
    order = ("info", "warning", "error", "critical")
    try:
        return order.index(level.lower()) >= order.index(minimum.lower())
    except ValueError:
        return True
