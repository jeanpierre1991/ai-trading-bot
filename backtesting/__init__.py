"""Historical simulation and backtesting framework."""

from __future__ import annotations

from backtesting.engine import BacktestEngine, BacktestResult
from backtesting.module import BacktestingModule

MODULE_CLASS = BacktestingModule

__all__ = ["BacktestingModule", "BacktestEngine", "BacktestResult", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(BacktestingModule)
