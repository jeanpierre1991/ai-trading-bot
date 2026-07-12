"""Tests for indicator calculator orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.types import MarketBar
from technical_analysis.calculator import IndicatorCalculator
from technical_analysis.indicators import calculate_sma
from technical_analysis.types import IndicatorRequest, IndicatorSource


def _make_bars(closes: list[str]) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for index, close in enumerate(closes):
        bars.append(
            MarketBar(
                timestamp=datetime(2026, 1, 1, 9, index, tzinfo=timezone.utc),
                open=Decimal(close),
                high=Decimal(close) + Decimal("1"),
                low=Decimal(close) - Decimal("1"),
                close=Decimal(close),
                volume=Decimal("1000"),
                symbol="AAPL",
                timeframe="1h",
            )
        )
    return bars


def test_compute_returns_empty_snapshot_for_no_requests() -> None:
    bars = _make_bars(["100", "101"])

    snapshot = IndicatorCalculator.compute(bars, [])

    assert snapshot.values == {}
    assert snapshot.series == {}
    assert snapshot.timestamp == bars[-1].timestamp


def test_compute_orchestrates_sma_using_existing_implementation() -> None:
    bars = _make_bars(["10", "20", "30"])
    closes = [bar.close for bar in bars]
    expected = calculate_sma(closes, 2)

    snapshot = IndicatorCalculator.compute(
        bars,
        [IndicatorRequest(name="sma", params={"period": 2})],
    )

    assert snapshot.series["sma_2"] == expected
    assert snapshot.values["sma_2"] == expected[-1]


def test_compute_supports_multiple_indicators() -> None:
    bars = _make_bars([str(100 + i) for i in range(30)])

    snapshot = IndicatorCalculator.compute(
        bars,
        [
            IndicatorRequest(name="sma", params={"period": 5}),
            IndicatorRequest(name="rsi", params={"period": 14}),
        ],
    )

    assert "sma_5" in snapshot.series
    assert "rsi_14" in snapshot.series
    assert snapshot.values["sma_5"] is not None
    assert snapshot.values["rsi_14"] is not None


def test_compute_uses_requested_source() -> None:
    bars = [
        MarketBar(
            timestamp=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
            open=Decimal("10"),
            high=Decimal("50"),
            low=Decimal("5"),
            close=Decimal("10"),
            volume=Decimal("1000"),
            symbol="AAPL",
            timeframe="1h",
        ),
        MarketBar(
            timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            open=Decimal("10"),
            high=Decimal("60"),
            low=Decimal("8"),
            close=Decimal("10"),
            volume=Decimal("1000"),
            symbol="AAPL",
            timeframe="1h",
        ),
    ]
    highs = [bar.high for bar in bars]
    expected = calculate_sma(highs, 2)

    snapshot = IndicatorCalculator.compute(
        bars,
        [IndicatorRequest(name="sma", params={"period": 2}, source=IndicatorSource.HIGH)],
    )

    assert snapshot.series["sma_2"] == expected


def test_compute_raises_for_unknown_indicator() -> None:
    bars = _make_bars(["100", "101"])

    with pytest.raises(ValueError, match="Unknown indicator: 'macd'"):
        IndicatorCalculator.compute(bars, [IndicatorRequest(name="macd")])


def test_compute_raises_for_invalid_period() -> None:
    bars = _make_bars(["100", "101"])

    with pytest.raises(ValueError, match="period must be positive"):
        IndicatorCalculator.compute(
            bars,
            [IndicatorRequest(name="sma", params={"period": 0})],
        )


def test_compute_handles_empty_bars() -> None:
    snapshot = IndicatorCalculator.compute(
        [],
        [IndicatorRequest(name="sma", params={"period": 2})],
    )

    assert snapshot.timestamp is None
    assert snapshot.series["sma_2"] == []
    assert snapshot.values["sma_2"] is None


def test_compute_resolves_symbol_and_timeframe_from_bars() -> None:
    bars = _make_bars(["100", "101", "102"])

    snapshot = IndicatorCalculator.compute(
        bars,
        [IndicatorRequest(name="sma", params={"period": 2})],
    )

    assert snapshot.symbol == "AAPL"
    assert snapshot.timeframe == "1h"
