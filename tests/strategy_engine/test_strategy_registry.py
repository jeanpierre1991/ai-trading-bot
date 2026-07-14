"""Tests for the strategy registry."""

from __future__ import annotations

import pytest

from strategy_engine.registry import (
    STRATEGY_REGISTRY,
    create_strategy,
    default_strategy_name,
    get_strategy,
    list_strategies,
)
from strategy_engine.strategies import EmaCrossoverStrategy


def test_registry_contains_ema_crossover() -> None:
    assert "ema_crossover" in STRATEGY_REGISTRY
    assert list_strategies() == ("ema_crossover",)


def test_default_strategy_name_comes_from_registry() -> None:
    assert default_strategy_name() == list_strategies()[0]
    assert default_strategy_name() in STRATEGY_REGISTRY


def test_get_strategy_returns_definition() -> None:
    definition = get_strategy("EMA_Crossover")

    assert definition.name == "ema_crossover"
    assert definition.strategy_class is EmaCrossoverStrategy
    assert definition.default_params == {"fast_period": 12, "slow_period": 26}


def test_get_strategy_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        get_strategy("momentum")


def test_create_strategy_uses_defaults_and_overrides() -> None:
    default = create_strategy("ema_crossover")
    custom = create_strategy("ema_crossover", fast_period=5, slow_period=10)

    assert isinstance(default, EmaCrossoverStrategy)
    assert default.fast_period == 12
    assert default.slow_period == 26
    assert custom.fast_period == 5
    assert custom.slow_period == 10
