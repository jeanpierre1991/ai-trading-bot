"""Abstract base class and health-check contracts for all modules."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from config.settings import Settings


class ModuleStatus(str, Enum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class ModuleHealth:
    name: str
    status: ModuleStatus
    message: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.status in {ModuleStatus.READY, ModuleStatus.DEGRADED}


class BaseModule(ABC):
    """Contract every trading bot module must implement."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._status = ModuleStatus.UNINITIALIZED
        self._logger = logging.getLogger(f"trading_bot.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique module identifier."""

    @property
    def status(self) -> ModuleStatus:
        return self._status

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def initialize(self) -> None:
        """Initialize module resources. Override for custom setup."""
        self._status = ModuleStatus.INITIALIZING
        self._logger.info("Initializing module: %s", self.name)
        self._on_initialize()
        self._status = ModuleStatus.READY
        self._logger.info("Module ready: %s", self.name)

    def _on_initialize(self) -> None:
        """Hook for subclasses to perform initialization logic."""

    def shutdown(self) -> None:
        """Release module resources. Override for custom teardown."""
        self._logger.info("Shutting down module: %s", self.name)
        self._on_shutdown()
        self._status = ModuleStatus.SHUTDOWN

    def _on_shutdown(self) -> None:
        """Hook for subclasses to perform shutdown logic."""

    @abstractmethod
    def health_check(self) -> ModuleHealth:
        """Return current module health status."""

    def _healthy(self, message: str = "OK", **details: Any) -> ModuleHealth:
        return ModuleHealth(
            name=self.name,
            status=self._status,
            message=message,
            details=details,
        )

    def _unhealthy(self, message: str, **details: Any) -> ModuleHealth:
        return ModuleHealth(
            name=self.name,
            status=ModuleStatus.ERROR,
            message=message,
            details=details,
        )
