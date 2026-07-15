"""Portfolio tracking and performance metrics."""

from __future__ import annotations

from portfolio_manager.module import PortfolioManagerModule
from portfolio_manager.portfolio import Fill, Portfolio, Position

MODULE_CLASS = PortfolioManagerModule

__all__ = ["PortfolioManagerModule", "Portfolio", "Position", "Fill", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(PortfolioManagerModule)
