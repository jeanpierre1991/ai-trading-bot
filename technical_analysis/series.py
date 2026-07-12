"""OHLCV series extraction from market bars."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from core.types import MarketBar
from technical_analysis.types import IndicatorSource

_EXTRACTORS: dict[IndicatorSource, Callable[[MarketBar], Decimal]] = {
    IndicatorSource.CLOSE: lambda bar: bar.close,
    IndicatorSource.HIGH: lambda bar: bar.high,
    IndicatorSource.LOW: lambda bar: bar.low,
    IndicatorSource.VOLUME: lambda bar: bar.volume,
}


def extract_series(bars: list[MarketBar], source: IndicatorSource) -> list[Decimal]:
    if not bars:
        return []
    extractor = _EXTRACTORS[source]
    return [extractor(bar) for bar in bars]


def extract_closes(bars: list[MarketBar]) -> list[Decimal]:
    return extract_series(bars, IndicatorSource.CLOSE)


def extract_highs(bars: list[MarketBar]) -> list[Decimal]:
    return extract_series(bars, IndicatorSource.HIGH)


def extract_lows(bars: list[MarketBar]) -> list[Decimal]:
    return extract_series(bars, IndicatorSource.LOW)


def extract_volumes(bars: list[MarketBar]) -> list[Decimal]:
    return extract_series(bars, IndicatorSource.VOLUME)
