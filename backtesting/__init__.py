"""Historical simulation and backtesting framework."""

from __future__ import annotations

from backtesting.bars_io import load_bars_from_csv, make_synthetic_bars
from backtesting.commission import CommissionDryRunExecutor
from backtesting.engine import BacktestEngine, BacktestResult
from backtesting.module import BacktestingModule
from backtesting.runner import (
    MAX_BACKTEST_CYCLES,
    BacktestConfig,
    BacktestRunner,
)

MODULE_CLASS = BacktestingModule

__all__ = [
    "BacktestingModule",
    "BacktestEngine",
    "BacktestResult",
    "BacktestConfig",
    "BacktestRunner",
    "CommissionDryRunExecutor",
    "MAX_BACKTEST_CYCLES",
    "load_bars_from_csv",
    "make_synthetic_bars",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(BacktestingModule)
