"""Webhook alert notifier (Milestone 14.2).

Broker-agnostic HTTP POST of alert payloads. Transport is injectable so tests
can mock network without coupling to a specific HTTP client.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Protocol

from alerts.notifier import Alert, AlertNotifier

_logger = logging.getLogger("trading_bot.alerts.webhook")


class WebhookHttpTransport(Protocol):
    """Minimal HTTP port used by WebhookNotifier (injectable)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> Any:
        """Return an object with ``status_code`` (int). May raise on network failure."""


class WebhookNotifier(AlertNotifier):
    """POST alert JSON to a configured webhook URL."""

    def __init__(
        self,
        *,
        url: str,
        transport: WebhookHttpTransport,
        timeout_seconds: float = 5.0,
        min_level: str | None = None,
    ) -> None:
        self._url = str(url or "").strip()
        self._transport = transport
        self._timeout_seconds = float(timeout_seconds)
        self._min_level = (min_level or "").strip().lower() or None
        self._sent_count = 0
        self._failure_count = 0

    @property
    def url(self) -> str:
        return self._url

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def send(self, alert: Alert) -> bool:
        if not self._url:
            self._failure_count += 1
            _logger.warning("webhook_notifier_skipped reason=missing_url")
            return False
        if self._min_level and not _level_at_least(alert.level.value, self._min_level):
            return False
        payload = {
            "title": alert.title,
            "message": alert.message,
            "level": alert.level.value,
            "source": alert.source,
            "timestamp": alert.timestamp.isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = self._transport.request(
                "POST",
                self._url,
                headers=headers,
                body=body,
                timeout=self._timeout_seconds,
            )
            status = int(getattr(response, "status_code", 0))
        except Exception as exc:  # noqa: BLE001 - never raise into callers
            self._failure_count += 1
            _logger.warning(
                "webhook_notifier_failed title=%s error=%s",
                alert.title,
                exc,
            )
            return False
        if 200 <= status < 300:
            self._sent_count += 1
            return True
        self._failure_count += 1
        _logger.warning(
            "webhook_notifier_http_error title=%s status=%s",
            alert.title,
            status,
        )
        return False


def _level_at_least(level: str, minimum: str) -> bool:
    order = ("info", "warning", "error", "critical")
    try:
        return order.index(level.lower()) >= order.index(minimum.lower())
    except ValueError:
        return True
