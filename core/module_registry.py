"""Module discovery and registration system."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, TypeVar

from core.base_module import BaseModule
from core.exceptions import ModuleLoadError

if TYPE_CHECKING:
    from config.settings import Settings

T = TypeVar("T", bound=BaseModule)

logger = logging.getLogger("trading_bot.registry")

# Built-in module packages. New packages can be appended without changing core logic.
DEFAULT_MODULE_PACKAGES: tuple[str, ...] = (
    "market_data",
    "technical_analysis",
    "ai_engine",
    "news_engine",
    "risk_manager",
    "strategy_engine",
    "order_manager",
    "portfolio_manager",
    "broker_interface",
    "backtesting",
    "alerts",
)


class ModuleRegistry:
    """Discovers, registers, and instantiates trading bot modules."""

    def __init__(self, settings: Settings, packages: tuple[str, ...] | None = None) -> None:
        self._settings = settings
        self._packages = packages or DEFAULT_MODULE_PACKAGES
        self._module_classes: dict[str, type[BaseModule]] = {}
        self._instances: dict[str, BaseModule] = {}

    @property
    def registered_modules(self) -> dict[str, type[BaseModule]]:
        return dict(self._module_classes)

    @property
    def loaded_modules(self) -> dict[str, BaseModule]:
        return dict(self._instances)

    def register(self, module_cls: type[T]) -> type[T]:
        """Decorator to register a module class."""
        probe = module_cls(self._settings)
        self._module_classes[probe.name] = module_cls
        logger.debug("Registered module class: %s", probe.name)
        return module_cls

    def register_class(self, module_cls: type[BaseModule]) -> None:
        """Programmatically register a module class."""
        probe = module_cls(self._settings)
        self._module_classes[probe.name] = module_cls

    def discover(self) -> list[str]:
        """Import module packages and collect registered module classes."""
        discovered: list[str] = []

        for package_name in self._packages:
            try:
                package = importlib.import_module(package_name)
            except ImportError as exc:
                raise ModuleLoadError(f"Failed to import package '{package_name}': {exc}") from exc

            if hasattr(package, "register_modules"):
                package.register_modules(self)

            if hasattr(package, "MODULE_CLASS"):
                self.register_class(package.MODULE_CLASS)
                discovered.append(package.MODULE_CLASS(self._settings).name)
                continue

            for _, module_name, _ in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
                importlib.import_module(module_name)

            if hasattr(package, "get_module_class"):
                module_cls = package.get_module_class()
                self.register_class(module_cls)
                discovered.append(module_cls(self._settings).name)

        for name in self._module_classes:
            if name not in discovered:
                discovered.append(name)

        logger.info("Discovered %d module(s): %s", len(discovered), ", ".join(sorted(discovered)))
        return sorted(discovered)

    def load_all(self) -> dict[str, BaseModule]:
        """Instantiate and initialize all registered modules."""
        if not self._module_classes:
            self.discover()

        for name, module_cls in sorted(self._module_classes.items()):
            if name in self._instances:
                continue
            try:
                instance = module_cls(self._settings)
                instance.initialize()
                self._instances[name] = instance
            except Exception as exc:
                raise ModuleLoadError(f"Failed to load module '{name}': {exc}") from exc

        return self.loaded_modules

    def get(self, name: str) -> BaseModule:
        if name not in self._instances:
            raise ModuleLoadError(f"Module '{name}' is not loaded")
        return self._instances[name]

    def shutdown_all(self) -> None:
        for name in reversed(list(self._instances)):
            try:
                self._instances[name].shutdown()
            except Exception as exc:
                logger.error("Error shutting down module '%s': %s", name, exc)
        self._instances.clear()
