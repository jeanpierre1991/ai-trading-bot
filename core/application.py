"""Application orchestrator and startup lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth, ModuleStatus
from core.events import Event, EventBus
from core.exceptions import ModuleHealthError, ModuleLoadError
from core.module_registry import ModuleRegistry

logger = logging.getLogger("trading_bot.application")


@dataclass
class StartupReport:
    settings_loaded: bool
    modules_discovered: list[str] = field(default_factory=list)
    modules_loaded: list[str] = field(default_factory=list)
    health_results: list[ModuleHealth] = field(default_factory=list)
    success: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "AI TRADING BOT — STARTUP VERIFICATION",
            "=" * 60,
            f"Settings loaded: {'YES' if self.settings_loaded else 'NO'}",
            f"Modules discovered: {len(self.modules_discovered)}",
            f"Modules loaded: {len(self.modules_loaded)}",
            "",
            "Module Health:",
        ]

        for health in self.health_results:
            icon = "✓" if health.is_healthy else "✗"
            lines.append(f"  {icon} {health.name:25s} [{health.status.value:14s}] {health.message}")

        lines.append("")
        if self.errors:
            lines.append("Errors:")
            for error in self.errors:
                lines.append(f"  - {error}")
            lines.append("")

        lines.append(f"Overall status: {'READY' if self.success else 'FAILED'}")
        lines.append("=" * 60)
        return "\n".join(lines)


class TradingBotApplication:
    """Main application entry point and lifecycle manager."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._registry = ModuleRegistry(self._settings)
        self._event_bus = EventBus()
        self._started = False

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def registry(self) -> ModuleRegistry:
        return self._registry

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def is_started(self) -> bool:
        return self._started

    def get_module(self, name: str) -> BaseModule:
        return self._registry.get(name)

    def startup(self) -> StartupReport:
        """Discover, load, and verify all modules."""
        report = StartupReport(settings_loaded=True)

        try:
            report.modules_discovered = self._registry.discover()
            loaded = self._registry.load_all()
            report.modules_loaded = sorted(loaded.keys())

            for name in report.modules_loaded:
                module = loaded[name]
                health = module.health_check()
                report.health_results.append(health)
                if not health.is_healthy:
                    report.errors.append(f"Module '{name}' failed health check: {health.message}")

            report.success = len(report.errors) == 0 and len(report.modules_loaded) > 0
            self._started = report.success

            if report.success:
                self._event_bus.publish(
                    Event(
                        topic="application.started",
                        payload={"modules": report.modules_loaded},
                        source="application",
                    )
                )
                logger.info("Application startup completed successfully")
            else:
                logger.error("Application startup completed with errors")

        except (ModuleLoadError, ModuleHealthError) as exc:
            report.errors.append(str(exc))
            report.success = False
            logger.exception("Startup failed: %s", exc)
        except Exception as exc:
            report.errors.append(f"Unexpected error: {exc}")
            report.success = False
            logger.exception("Unexpected startup failure")

        return report

    def shutdown(self) -> None:
        logger.info("Shutting down application")
        self._registry.shutdown_all()
        self._event_bus.publish(Event(topic="application.shutdown", source="application"))
        self._event_bus.clear()
        self._started = False
        logger.info("Application shutdown complete")

    def run_health_checks(self) -> list[ModuleHealth]:
        results: list[ModuleHealth] = []
        for name, module in sorted(self._registry.loaded_modules.items()):
            health = module.health_check()
            results.append(health)
            if health.status == ModuleStatus.ERROR:
                logger.warning("Module '%s' health check failed: %s", name, health.message)
        return results
