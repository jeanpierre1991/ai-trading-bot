"""Tests for EmaCrossoverStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from core.types import MarketBar, SignalAction
from strategy_engine.strategies.ema_crossover import EmaCrossoverStrategy
from technical_analysis.calculator import IndicatorCalculator
from technical_analysis.types import IndicatorSnapshot


def _bars_from_closes(closes: list[Decimal]) -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(
            timestamp=base + timedelta(hours=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1000"),
            symbol="AAPL",
            timeframe="1h",
        )
        for index, close in enumerate(closes)
    ]


def test_rejects_invalid_periods() -> None:
    with pytest.raises(ValueError, match="positive"):
        EmaCrossoverStrategy(fast_period=0, slow_period=26)

    with pytest.raises(ValueError, match="fast_period must be less"):
        EmaCrossoverStrategy(fast_period=26, slow_period=12)


def test_empty_bars_return_hold() -> None:
    signal = EmaCrossoverStrategy(fast_period=2, slow_period=3).evaluate([], symbol="AAPL")

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.0
    assert signal.strategy_name == "ema_crossover"
    assert signal.metadata["note"] == "No market bars provided"


def test_insufficient_history_returns_hold() -> None:
    bars = _bars_from_closes([Decimal("100"), Decimal("101")])
    signal = EmaCrossoverStrategy(fast_period=2, slow_period=3).evaluate(bars, symbol="AAPL")

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.0
    assert "Insufficient EMA history" in str(signal.metadata["note"])


def test_buy_on_bullish_crossover() -> None:
    # Truncated at the bar where fast EMA crosses above slow EMA.
    closes = [
        Decimal(str(value))
        for value in [50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 40, 39, 38, 37, 36, 35, 34, 33, 32, 31, 31, 32, 33]
    ]
    signal = EmaCrossoverStrategy(fast_period=2, slow_period=4).evaluate(
        _bars_from_closes(closes),
        symbol="AAPL",
    )

    assert signal.action == SignalAction.BUY
    assert signal.symbol == "AAPL"
    assert signal.price == closes[-1]
    assert signal.confidence > 0.5
    assert "fast_ema" in signal.metadata
    assert "slow_ema" in signal.metadata


def test_sell_on_bearish_crossover() -> None:
    # Truncated at the bar where fast EMA crosses below slow EMA.
    closes = [
        Decimal(str(value))
        for value in [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 49, 48, 47]
    ]
    signal = EmaCrossoverStrategy(fast_period=2, slow_period=4).evaluate(
        _bars_from_closes(closes),
        symbol="AAPL",
    )

    assert signal.action == SignalAction.SELL
    assert signal.price == closes[-1]
    assert signal.confidence > 0.5


def test_hold_when_no_crossover() -> None:
    closes = [Decimal(str(100 + i)) for i in range(20)]
    signal = EmaCrossoverStrategy(fast_period=2, slow_period=4).evaluate(
        _bars_from_closes(closes),
        symbol="AAPL",
    )

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.5


def test_uses_indicator_calculator_not_manual_ema() -> None:
    strategy = EmaCrossoverStrategy(fast_period=2, slow_period=3)
    bars = _bars_from_closes([Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")])
    snapshot = IndicatorSnapshot(
        symbol="AAPL",
        timeframe="1h",
        timestamp=bars[-1].timestamp,
        values={"ema_2": Decimal("102.5"), "ema_3": Decimal("101.5")},
        series={
            "ema_2": [None, Decimal("100.5"), Decimal("100.0"), Decimal("102.5")],
            "ema_3": [None, None, Decimal("101.0"), Decimal("101.5")],
        },
    )

    with patch.object(IndicatorCalculator, "compute", return_value=snapshot) as compute:
        signal = strategy.evaluate(bars, symbol="AAPL")

    compute.assert_called_once()
    assert signal.action == SignalAction.BUY
    request_names = [request.name for request in compute.call_args.args[1]]
    assert request_names == ["ema", "ema"]
