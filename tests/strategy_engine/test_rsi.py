"""Tests for RSIStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from core.types import MarketBar, SignalAction
from strategy_engine.strategies.rsi import RSIStrategy
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


def test_rejects_invalid_params() -> None:
    with pytest.raises(ValueError, match="positive"):
        RSIStrategy(period=0)

    with pytest.raises(ValueError, match="oversold must be"):
        RSIStrategy(oversold=70.0, overbought=30.0)

    with pytest.raises(ValueError, match="oversold must be"):
        RSIStrategy(oversold=-1.0, overbought=70.0)


def test_empty_bars_return_hold() -> None:
    signal = RSIStrategy(period=5).evaluate([], symbol="AAPL")

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.0
    assert signal.strategy_name == "rsi"
    assert signal.metadata["note"] == "No market bars provided"


def test_insufficient_history_returns_hold() -> None:
    bars = _bars_from_closes([Decimal("100"), Decimal("101")])
    signal = RSIStrategy(period=14).evaluate(bars, symbol="AAPL")

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.0
    assert signal.metadata["note"] == "Insufficient RSI history"


def test_buy_when_oversold() -> None:
    closes = [Decimal(str(value)) for value in ([50 + i for i in range(20)] + [70 - i for i in range(25)])]
    signal = RSIStrategy(period=14).evaluate(_bars_from_closes(closes), symbol="AAPL")

    assert signal.action == SignalAction.BUY
    assert signal.symbol == "AAPL"
    assert signal.price == closes[-1]
    assert signal.confidence > 0.5
    assert float(signal.metadata["rsi"]) < 30.0


def test_sell_when_overbought() -> None:
    closes = [Decimal(str(value)) for value in ([100 - i for i in range(20)] + [80 + i for i in range(25)])]
    signal = RSIStrategy(period=14).evaluate(_bars_from_closes(closes), symbol="AAPL")

    assert signal.action == SignalAction.SELL
    assert signal.price == closes[-1]
    assert signal.confidence > 0.5
    assert float(signal.metadata["rsi"]) > 70.0


def test_hold_when_neutral() -> None:
    # Mild oscillation keeps RSI in the neutral band (not flat: flat closes yield RSI 100).
    closes = [Decimal(str(100 + (index % 3 - 1) * 0.1)) for index in range(40)]
    signal = RSIStrategy(period=14).evaluate(_bars_from_closes(closes), symbol="AAPL")

    assert signal.action == SignalAction.HOLD
    assert signal.confidence == 0.5
    assert 30.0 <= float(signal.metadata["rsi"]) <= 70.0


def test_uses_indicator_calculator_not_manual_rsi() -> None:
    strategy = RSIStrategy(period=14)
    bars = _bars_from_closes([Decimal(str(100 + i)) for i in range(20)])
    snapshot = IndicatorSnapshot(
        symbol="AAPL",
        timeframe="1h",
        timestamp=bars[-1].timestamp,
        values={"rsi_14": Decimal("25.0")},
        series={"rsi_14": [None] * 19 + [Decimal("25.0")]},
    )

    with patch.object(IndicatorCalculator, "compute", return_value=snapshot) as compute:
        signal = strategy.evaluate(bars, symbol="AAPL")

    compute.assert_called_once()
    assert signal.action == SignalAction.BUY
    request_names = [request.name for request in compute.call_args.args[1]]
    assert request_names == ["rsi"]


def test_custom_thresholds() -> None:
    strategy = RSIStrategy(period=14, oversold=40.0, overbought=60.0)
    bars = _bars_from_closes([Decimal(str(100 + i)) for i in range(20)])
    snapshot = IndicatorSnapshot(
        symbol="AAPL",
        timeframe="1h",
        timestamp=bars[-1].timestamp,
        values={"rsi_14": Decimal("35.0")},
        series={"rsi_14": [None] * 19 + [Decimal("35.0")]},
    )

    with patch.object(IndicatorCalculator, "compute", return_value=snapshot):
        signal = strategy.evaluate(bars, symbol="AAPL")

    assert signal.action == SignalAction.BUY
    assert signal.metadata["oversold"] == 40.0
    assert signal.metadata["overbought"] == 60.0
