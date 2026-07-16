"""Strategy registry mapping names to implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy_engine.base import BaseStrategy
from strategy_engine.strategies.ema_crossover_strategy import EMACrossoverStrategy
from strategy_engine.strategies.rsi import RSIStrategy


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    strategy_class: type[BaseStrategy]
    default_params: dict[str, Any]


STRATEGY_REGISTRY: dict[str, StrategyDefinition] = {
    "ema_crossover": StrategyDefinition(
        name="ema_crossover",
        strategy_class=EMACrossoverStrategy,
        default_params={"fast_period": 12, "slow_period": 26},
    ),
    "rsi": StrategyDefinition(
        name="rsi",
        strategy_class=RSIStrategy,
        default_params={"period": 14, "oversold": 30.0, "overbought": 70.0},
    ),
}


def get_strategy(name: str) -> StrategyDefinition:
    normalized_name = name.strip().lower()
    try:
        return STRATEGY_REGISTRY[normalized_name]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy: '{name}'") from exc


def list_strategies() -> tuple[str, ...]:
    return tuple(sorted(STRATEGY_REGISTRY))


def default_strategy_name() -> str:
    """Return the deterministic default strategy from the registry."""
    names = list_strategies()
    if not names:
        raise ValueError("No strategies registered")
    return names[0]


def create_strategy(name: str, **overrides: Any) -> BaseStrategy:
    definition = get_strategy(name)
    params = {**definition.default_params, **overrides}
    return definition.strategy_class(**params)
