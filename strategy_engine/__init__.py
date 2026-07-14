"""Trading strategy orchestration."""

from __future__ import annotations

from strategy_engine.base import BaseStrategy
from strategy_engine.module import StrategyEngineModule
from strategy_engine.registry import (
    STRATEGY_REGISTRY,
    StrategyDefinition,
    create_strategy,
    get_strategy,
    list_strategies,
)
from strategy_engine.signal import StrategySignal
from strategy_engine.strategies import EmaCrossoverStrategy

MODULE_CLASS = StrategyEngineModule

__all__ = [
    "BaseStrategy",
    "EmaCrossoverStrategy",
    "MODULE_CLASS",
    "STRATEGY_REGISTRY",
    "StrategyDefinition",
    "StrategyEngineModule",
    "StrategySignal",
    "create_strategy",
    "get_strategy",
    "list_strategies",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(StrategyEngineModule)
