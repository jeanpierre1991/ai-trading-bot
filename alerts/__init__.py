"""Alerting and notification system."""

from __future__ import annotations

from alerts.module import AlertsModule
from alerts.notifier import Alert, AlertLevel, AlertNotifier, ConsoleNotifier

MODULE_CLASS = AlertsModule

__all__ = ["AlertsModule", "AlertNotifier", "ConsoleNotifier", "Alert", "AlertLevel", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(AlertsModule)
