"""Tests for OHLCV series extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.types import MarketBar
from technical_analysis.series import (
    extract_closes,
    extract_highs,
    extract_lows,
    extract_series,
    extract_volumes,
)
from technical_analysis.types import IndicatorSource


def _make_bar(
    *,
    close: str = "100.00",
    high: str = "101.00",
    low: str = "99.00",
    volume: str = "1000",
) -> MarketBar:
    return MarketBar(
        timestamp=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        open=Decimal("100.00"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        symbol="AAPL",
        timeframe="1h",
    )


def test_extract_series_returns_empty_list_for_empty_bars() -> None:
    assert extract_series([], IndicatorSource.CLOSE) == []


def test_extract_closes() -> None:
    bars = [_make_bar(close="100.00"), _make_bar(close="101.50")]

    assert extract_closes(bars) == [Decimal("100.00"), Decimal("101.50")]


def test_extract_highs() -> None:
    bars = [_make_bar(high="102.00"), _make_bar(high="103.25")]

    assert extract_highs(bars) == [Decimal("102.00"), Decimal("103.25")]


def test_extract_lows() -> None:
    bars = [_make_bar(low="98.00"), _make_bar(low="97.50")]

    assert extract_lows(bars) == [Decimal("98.00"), Decimal("97.50")]


def test_extract_volumes() -> None:
    bars = [_make_bar(volume="1000"), _make_bar(volume="1500")]

    assert extract_volumes(bars) == [Decimal("1000"), Decimal("1500")]
