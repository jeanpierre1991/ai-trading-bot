"""M14.2 webhook/email/fanout notifier contract tests (mocked transports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from alerts.email import EmailNotifier
from alerts.notifier import Alert, AlertLevel, AlertNotifier, ConsoleNotifier, FanoutNotifier
from alerts.webhook import WebhookNotifier
from alerts.wiring import build_alert_notifier_from_settings
from config.settings import Settings


@dataclass
class FakeHttpResponse:
    status_code: int
    body: bytes = b""


class RecordingHttpTransport:
    def __init__(
        self,
        *,
        status_code: int = 200,
        raise_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> FakeHttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
            }
        )
        if self.raise_error is not None:
            raise self.raise_error
        return FakeHttpResponse(status_code=self.status_code)


class RecordingSmtpTransport:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.raise_error = raise_error
        self.messages: list[Any] = []

    def send(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        message: Any,
        timeout: float = 10.0,
    ) -> None:
        if self.raise_error is not None:
            raise self.raise_error
        self.messages.append(
            {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "use_tls": use_tls,
                "message": message,
                "timeout": timeout,
            }
        )


class BoomNotifier(AlertNotifier):
    def send(self, alert: Alert) -> bool:
        raise RuntimeError("channel boom")


def _critical() -> Alert:
    return Alert(
        title="emergency_stop_activated",
        message="incident_id=abc reason=kill",
        level=AlertLevel.CRITICAL,
        source="emergency_stop",
    )


def test_webhook_notifier_posts_json_on_success() -> None:
    transport = RecordingHttpTransport(status_code=204)
    notifier = WebhookNotifier(
        url="https://hooks.example/alert",
        transport=transport,
        timeout_seconds=3.0,
    )
    assert notifier.send(_critical()) is True
    assert notifier.sent_count == 1
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://hooks.example/alert"
    assert call["timeout"] == 3.0
    assert b"emergency_stop_activated" in call["body"]
    assert b"critical" in call["body"]


def test_webhook_notifier_network_failure_returns_false() -> None:
    transport = RecordingHttpTransport(raise_error=TimeoutError("offline"))
    notifier = WebhookNotifier(url="https://hooks.example/alert", transport=transport)
    assert notifier.send(_critical()) is False
    assert notifier.failure_count == 1


def test_webhook_notifier_http_error_returns_false() -> None:
    transport = RecordingHttpTransport(status_code=500)
    notifier = WebhookNotifier(url="https://hooks.example/alert", transport=transport)
    assert notifier.send(_critical()) is False
    assert notifier.failure_count == 1


def test_email_notifier_sends_via_smtp_transport() -> None:
    smtp = RecordingSmtpTransport()
    notifier = EmailNotifier(
        to_address="ops@example.com",
        from_address="bot@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        transport=smtp,
    )
    assert notifier.send(_critical()) is True
    assert notifier.sent_count == 1
    assert len(smtp.messages) == 1
    msg = smtp.messages[0]
    assert msg["host"] == "smtp.example.com"
    assert msg["message"]["To"] == "ops@example.com"
    assert "CRITICAL" in msg["message"]["Subject"]
    # Password must not appear in alert body content.
    body = msg["message"].get_content()
    assert "secret" not in body


def test_email_notifier_incomplete_config_fail_closed() -> None:
    smtp = RecordingSmtpTransport()
    notifier = EmailNotifier(
        to_address="ops@example.com",
        from_address="",
        smtp_host="",
        transport=smtp,
    )
    assert notifier.send(_critical()) is False
    assert smtp.messages == []


def test_email_notifier_smtp_failure_returns_false() -> None:
    smtp = RecordingSmtpTransport(raise_error=ConnectionError("smtp down"))
    notifier = EmailNotifier(
        to_address="ops@example.com",
        from_address="bot@example.com",
        smtp_host="smtp.example.com",
        transport=smtp,
    )
    assert notifier.send(_critical()) is False
    assert notifier.failure_count == 1


def test_fanout_continues_when_one_channel_raises() -> None:
    smtp = RecordingSmtpTransport()
    email = EmailNotifier(
        to_address="ops@example.com",
        from_address="bot@example.com",
        smtp_host="smtp.example.com",
        transport=smtp,
    )
    fanout = FanoutNotifier([BoomNotifier(), email, ConsoleNotifier()])
    assert fanout.send(_critical()) is True
    assert len(smtp.messages) == 1
    assert fanout.failure_count >= 1


def test_fanout_returns_false_when_all_channels_fail() -> None:
    transport = RecordingHttpTransport(status_code=503)
    fanout = FanoutNotifier(
        [
            WebhookNotifier(url="https://hooks.example/x", transport=transport),
            BoomNotifier(),
        ]
    )
    assert fanout.send(_critical()) is False


def test_wiring_console_only_when_remote_channels_unset() -> None:
    settings = Settings(alerts_enabled=True)
    notifier = build_alert_notifier_from_settings(settings)
    assert isinstance(notifier, ConsoleNotifier)


def test_wiring_includes_webhook_and_email_when_configured() -> None:
    settings = Settings(
        alerts_enabled=True,
        alert_webhook_url="https://hooks.example/alert",
        alert_email="ops@example.com",
        alert_email_from="bot@example.com",
        alert_smtp_host="smtp.example.com",
    )
    http = RecordingHttpTransport()
    smtp = RecordingSmtpTransport()
    notifier = build_alert_notifier_from_settings(
        settings,
        http_transport=http,
        smtp_transport=smtp,
    )
    assert isinstance(notifier, FanoutNotifier)
    assert notifier.send(_critical()) is True
    assert len(http.calls) == 1
    assert len(smtp.messages) == 1


def test_webhook_min_level_filters_info() -> None:
    transport = RecordingHttpTransport()
    notifier = WebhookNotifier(
        url="https://hooks.example/alert",
        transport=transport,
        min_level="critical",
    )
    info = Alert(title="booking_success", message="ok", level=AlertLevel.INFO)
    assert notifier.send(info) is False
    assert transport.calls == []
    assert notifier.send(_critical()) is True
