"""Yahoo Finance market data provider backed by yfinance."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import yfinance as yf

from core.types import MarketBar, Symbol, TimeFrame
from market_data.provider import MarketDataProvider

_TIMEFRAME_INTERVALS: dict[TimeFrame, str] = {
    TimeFrame.M1: "1m",
    TimeFrame.M5: "5m",
    TimeFrame.M15: "15m",
    TimeFrame.H1: "1h",
    TimeFrame.D1: "1d",
    TimeFrame.W1: "1wk",
}

_INTERVAL_MINUTES: dict[TimeFrame, int] = {
    TimeFrame.M1: 1,
    TimeFrame.M5: 5,
    TimeFrame.M15: 15,
    TimeFrame.H1: 60,
    TimeFrame.H4: 240,
    TimeFrame.D1: 1440,
    TimeFrame.W1: 10080,
}


class YahooFinanceProvider(MarketDataProvider):
    """Fetches live prices and OHLCV bars from Yahoo Finance."""

    def __init__(self, ticker_factory: Callable[[str], Any] | None = None) -> None:
        self._ticker_factory = ticker_factory or yf.Ticker

    def get_latest_price(self, symbol: Symbol) -> Decimal:
        ticker = self._ticker_factory(str(symbol))
        price = ticker.fast_info.get("last_price")

        if price is None:
            history = ticker.history(period="5d", interval="1d")
            if history.empty:
                raise ValueError(f"No price data available for symbol '{symbol}'")
            price = history["Close"].iloc[-1]

        return Decimal(str(price)).quantize(Decimal("0.01"))

    def get_bars(self, symbol: Symbol, timeframe: TimeFrame, limit: int) -> list[MarketBar]:
        if limit <= 0:
            return []

        ticker = self._ticker_factory(str(symbol))
        interval = _timeframe_to_yfinance_interval(timeframe)
        bars_needed = limit * 4 if timeframe == TimeFrame.H4 else limit
        period = _compute_history_period(timeframe, bars_needed)
        history = ticker.history(period=period, interval=interval)

        if history.empty:
            raise ValueError(f"No bar data available for symbol '{symbol}'")

        if timeframe == TimeFrame.H4:
            history = _aggregate_hourly_to_h4(history)

        history = history.tail(limit)
        bars: list[MarketBar] = []

        for timestamp, row in history.iterrows():
            bars.append(_row_to_market_bar(_ensure_utc(timestamp), row, symbol, timeframe))

        return bars


def _timeframe_to_yfinance_interval(timeframe: TimeFrame) -> str:
    if timeframe == TimeFrame.H4:
        return _TIMEFRAME_INTERVALS[TimeFrame.H1]
    return _TIMEFRAME_INTERVALS[timeframe]


def _compute_history_period(timeframe: TimeFrame, limit: int) -> str:
    if timeframe == TimeFrame.M1:
        return "7d"
    if timeframe in (TimeFrame.M5, TimeFrame.M15):
        return "60d"

    source_timeframe = TimeFrame.H1 if timeframe == TimeFrame.H4 else timeframe
    minutes_per_bar = _INTERVAL_MINUTES[source_timeframe]
    total_minutes = minutes_per_bar * max(limit, 1)
    days = max(1, total_minutes // (24 * 60) + 2)

    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


def _aggregate_hourly_to_h4(history: Any) -> Any:
    if history.index.tz is None:
        indexed = history.copy()
        indexed.index = indexed.index.tz_localize("UTC")
    else:
        indexed = history.copy()
        indexed.index = indexed.index.tz_convert("UTC")

    return indexed.resample("4h").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    ).dropna()


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _row_to_market_bar(
    timestamp: datetime,
    row: Any,
    symbol: Symbol,
    timeframe: TimeFrame,
) -> MarketBar:
    volume = row["Volume"]
    if volume != volume:
        volume_value = Decimal("0")
    else:
        volume_value = Decimal(str(volume))

    return MarketBar(
        timestamp=timestamp,
        open=Decimal(str(row["Open"])).quantize(Decimal("0.01")),
        high=Decimal(str(row["High"])).quantize(Decimal("0.01")),
        low=Decimal(str(row["Low"])).quantize(Decimal("0.01")),
        close=Decimal(str(row["Close"])).quantize(Decimal("0.01")),
        volume=volume_value,
        symbol=symbol,
        timeframe=timeframe.value,
    )
