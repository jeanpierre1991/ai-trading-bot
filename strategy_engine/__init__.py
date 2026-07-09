"""Trading strategy orchestration."""

from __future__ import annotations

from strategy_engine.module import StrategyEngineModule
from strategy_engine.signal import StrategySignal

MODULE_CLASS = StrategyEngineModule

__all__ = ["StrategyEngineModule", "StrategySignal", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(StrategyEngineModule)
