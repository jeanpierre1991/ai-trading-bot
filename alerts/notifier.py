"""Alert notification abstractions."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    title: str
    message: str
    level: AlertLevel = AlertLevel.INFO
    source: str = "system"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format(self) -> str:
        return f"[{self.level.value.upper()}] {self.title}: {self.message}"


class AlertNotifier(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> bool:
        ...


class ConsoleNotifier(AlertNotifier):
    """Logs alerts to the application logger."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("trading_bot.alerts")
        self._sent_count = 0

    @property
    def sent_count(self) -> int:
        return self._sent_count

    def send(self, alert: Alert) -> bool:
        log_fn = {
            AlertLevel.INFO: self._logger.info,
            AlertLevel.WARNING: self._logger.warning,
            AlertLevel.ERROR: self._logger.error,
            AlertLevel.CRITICAL: self._logger.critical,
        }[alert.level]
        log_fn(alert.format())
        self._sent_count += 1
        return True


class FanoutNotifier(AlertNotifier):
    """Deliver one alert to multiple channels; never raises on channel failure.

    Returns ``True`` if at least one channel reports success. Individual
    channel exceptions are logged and swallowed so alert outages cannot
    interrupt emergency-stop or trading-cycle control flow.
    """

    def __init__(self, channels: list[AlertNotifier]) -> None:
        if not channels:
            raise ValueError("FanoutNotifier requires at least one channel")
        self._channels = list(channels)
        self._logger = logging.getLogger("trading_bot.alerts.fanout")
        self._sent_count = 0
        self._failure_count = 0

    @property
    def channels(self) -> tuple[AlertNotifier, ...]:
        return tuple(self._channels)

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def send(self, alert: Alert) -> bool:
        any_ok = False
        for channel in self._channels:
            try:
                ok = bool(channel.send(alert))
            except Exception as exc:  # noqa: BLE001 - channel must not abort callers
                self._failure_count += 1
                self._logger.warning(
                    "fanout_channel_failed channel=%s title=%s error=%s",
                    type(channel).__name__,
                    alert.title,
                    exc,
                )
                continue
            if ok:
                any_ok = True
            else:
                self._failure_count += 1
        if any_ok:
            self._sent_count += 1
        return any_ok
