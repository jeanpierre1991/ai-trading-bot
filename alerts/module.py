"""Alerts module implementation."""

from __future__ import annotations

from alerts.notifier import Alert, AlertLevel, AlertNotifier
from alerts.wiring import build_alert_notifier_from_settings
from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth


class AlertsModule(BaseModule):
    """Dispatches alerts through configured notification channels."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._notifiers: list[AlertNotifier] = []

    @property
    def name(self) -> str:
        return "alerts"

    def _on_initialize(self) -> None:
        if self._settings.alerts_enabled:
            notifier = build_alert_notifier_from_settings(self._settings)
            self._notifiers.append(notifier)

    def notify(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> int:
        alert = Alert(title=title, message=message, level=level, source=self.name)
        sent = 0
        for notifier in self._notifiers:
            try:
                if notifier.send(alert):
                    sent += 1
            except Exception:
                # Channel failures must not break module health/cycle callers.
                continue
        return sent

    def health_check(self) -> ModuleHealth:
        try:
            if not self._settings.alerts_enabled:
                return self._healthy(
                    message="Alerts disabled by configuration",
                    enabled=False,
                )

            sent = self.notify(
                title="Health Check",
                message="Alerts module operational",
                level=AlertLevel.INFO,
            )
            return self._healthy(
                message="Alert notifiers operational",
                enabled=True,
                notifiers=len(self._notifiers),
                test_alerts_sent=sent,
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
