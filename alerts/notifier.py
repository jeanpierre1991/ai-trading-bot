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
