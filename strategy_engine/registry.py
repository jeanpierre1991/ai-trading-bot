"""Strategy registry mapping names to implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy_engine.base import BaseStrategy
from strategy_engine.strategies.ema_crossover import EmaCrossoverStrategy


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    strategy_class: type[BaseStrategy]
    default_params: dict[str, Any]


STRATEGY_REGISTRY: dict[str, StrategyDefinition] = {
    "ema_crossover": StrategyDefinition(
        name="ema_crossover",
        strategy_class=EmaCrossoverStrategy,
        default_params={"fast_period": 12, "slow_period": 26},
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


def create_strategy(name: str, **overrides: Any) -> BaseStrategy:
    definition = get_strategy(name)
    params = {**definition.default_params, **overrides}
    return definition.strategy_class(**params)
