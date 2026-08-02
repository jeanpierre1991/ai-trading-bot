"""Alerting and notification system."""

from __future__ import annotations

from alerts.email import EmailNotifier
from alerts.module import AlertsModule
from alerts.notifier import Alert, AlertLevel, AlertNotifier, ConsoleNotifier, FanoutNotifier
from alerts.webhook import WebhookNotifier
from alerts.wiring import build_alert_notifier_from_settings

MODULE_CLASS = AlertsModule

__all__ = [
    "AlertsModule",
    "AlertNotifier",
    "ConsoleNotifier",
    "FanoutNotifier",
    "WebhookNotifier",
    "EmailNotifier",
    "Alert",
    "AlertLevel",
    "build_alert_notifier_from_settings",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(AlertsModule)
