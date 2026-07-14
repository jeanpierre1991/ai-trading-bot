"""Tests for StrategyEngineModule integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from config.settings import Settings
from core.base_module import ModuleStatus
from core.types import MarketBar, SignalAction
from strategy_engine.module import StrategyEngineModule
from strategy_engine.registry import default_strategy_name, list_strategies


@pytest.fixture
def module() -> StrategyEngineModule:
    engine = StrategyEngineModule(Settings())
    engine.initialize()
    return engine


def _sample_bars(count: int = 60) -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = Decimal("100")
    bars: list[MarketBar] = []
    for index in range(count):
        price = (price + Decimal("0.25")).quantize(Decimal("0.01"))
        bars.append(
            MarketBar(
                timestamp=base + timedelta(hours=index),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1000"),
            )
        )
    return bars


def test_initialize_registers_strategies_from_registry(module: StrategyEngineModule) -> None:
    assert module.list_strategies() == list(list_strategies())
    assert module.get_strategy(default_strategy_name()).name == default_strategy_name()


def test_evaluate_returns_strategy_signal(module: StrategyEngineModule) -> None:
    signal = module.evaluate(_sample_bars(), symbol="MSFT")

    assert signal.symbol == "MSFT"
    assert signal.strategy_name == default_strategy_name()
    assert signal.action in {SignalAction.BUY, SignalAction.SELL, SignalAction.HOLD}
    assert signal.price > 0


def test_evaluate_selects_strategy_by_name(module: StrategyEngineModule) -> None:
    strategy_name = default_strategy_name()
    signal = module.evaluate(_sample_bars(), strategy_name=strategy_name)

    assert signal.strategy_name == strategy_name


def test_evaluate_uses_default_symbol(module: StrategyEngineModule) -> None:
    signal = module.evaluate(_sample_bars())

    assert signal.symbol == "AAPL"


def test_evaluate_before_initialize_raises() -> None:
    engine = StrategyEngineModule(Settings())

    with pytest.raises(RuntimeError, match="not initialized"):
        engine.evaluate(_sample_bars())


def test_unknown_strategy_raises(module: StrategyEngineModule) -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        module.evaluate(_sample_bars(), strategy_name="mean_reversion")


def test_health_check_is_healthy(module: StrategyEngineModule) -> None:
    health = module.health_check()

    assert health.is_healthy is True
    assert health.status == ModuleStatus.READY
    assert health.message == "Strategy engine operational"
    assert health.details["strategies"] == list(list_strategies())
    assert health.details["sample_signal"]["strategy_name"] == default_strategy_name()
