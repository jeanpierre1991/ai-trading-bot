"""Portfolio tracking and performance metrics."""

from __future__ import annotations

from portfolio_manager.module import PortfolioManagerModule
from portfolio_manager.portfolio import Portfolio, Position

MODULE_CLASS = PortfolioManagerModule

__all__ = ["PortfolioManagerModule", "Portfolio", "Position", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(PortfolioManagerModule)
