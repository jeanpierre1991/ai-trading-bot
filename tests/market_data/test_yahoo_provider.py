"""Unit tests for YahooFinanceProvider using mocked tickers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core.types import MarketBar, Symbol, TimeFrame
from market_data.yahoo_provider import (
    YahooFinanceProvider,
    _ensure_utc,
    _row_to_market_bar,
    _timeframe_to_yfinance_interval,
)


class MockTicker:
    def __init__(
        self,
        symbol: str,
        *,
        last_price: float | None = 100.0,
        history_df: pd.DataFrame | None = None,
    ) -> None:
        self.symbol = symbol
        self.fast_info: dict[str, Any] = {"last_price": last_price}
        self.history_df = history_df if history_df is not None else pd.DataFrame()
        self.history_calls: list[dict[str, str]] = []

    def history(self, period: str, interval: str) -> pd.DataFrame:
        self.history_calls.append({"period": period, "interval": interval})
        return self.history_df


def _make_history_df(
    *,
    rows: int = 3,
    start: datetime | None = None,
    tz: timezone | ZoneInfo | None = timezone.utc,
    volume: float | list[float] = 1000.0,
) -> pd.DataFrame:
    if tz is None:
        start_time = start or datetime(2026, 1, 1, 9, 0)
        if start_time.tzinfo is not None:
            start_time = start_time.replace(tzinfo=None)
    else:
        start_time = start or datetime(2026, 1, 1, 9, 0, tzinfo=tz)
    timestamps = [start_time + timedelta(hours=index) for index in range(rows)]

    if isinstance(volume, list):
        volumes = volume
    else:
        volumes = [volume] * rows

    return pd.DataFrame(
        {
            "Open": [100.0 + index for index in range(rows)],
            "High": [101.5 + index for index in range(rows)],
            "Low": [99.2 + index for index in range(rows)],
            "Close": [100.8 + index for index in range(rows)],
            "Volume": volumes,
        },
        index=pd.DatetimeIndex(timestamps, tz=tz),
    )


def _provider_with_ticker(ticker: MockTicker) -> YahooFinanceProvider:
    return YahooFinanceProvider(ticker_factory=lambda _symbol: ticker)


def test_get_latest_price_uses_fast_info() -> None:
    ticker = MockTicker("AAPL", last_price=190.125)
    provider = _provider_with_ticker(ticker)

    price = provider.get_latest_price(Symbol("AAPL"))

    assert price == Decimal("190.12")
    assert ticker.history_calls == []


def test_get_latest_price_falls_back_to_history_close() -> None:
    history = _make_history_df(rows=2)
    history.loc[history.index[-1], "Close"] = 187.559
    ticker = MockTicker("AAPL", last_price=None, history_df=history)
    provider = _provider_with_ticker(ticker)

    price = provider.get_latest_price(Symbol("AAPL"))

    assert price == Decimal("187.56")
    assert ticker.history_calls == [{"period": "5d", "interval": "1d"}]


def test_get_latest_price_raises_on_empty_dataframe() -> None:
    ticker = MockTicker("AAPL", last_price=None, history_df=pd.DataFrame())
    provider = _provider_with_ticker(ticker)

    with pytest.raises(ValueError, match="No price data available for symbol 'AAPL'"):
        provider.get_latest_price(Symbol("AAPL"))


def test_get_latest_price_quantizes_decimal() -> None:
    ticker = MockTicker("AAPL", last_price=123.456789)
    provider = _provider_with_ticker(ticker)

    price = provider.get_latest_price(Symbol("AAPL"))

    assert price == Decimal("123.46")


def test_get_bars_returns_empty_list_when_limit_is_zero() -> None:
    ticker = MockTicker("AAPL", history_df=_make_history_df())
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 0)

    assert bars == []
    assert ticker.history_calls == []


def test_get_bars_returns_empty_list_when_limit_is_negative() -> None:
    ticker = MockTicker("AAPL", history_df=_make_history_df())
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, -5)

    assert bars == []
    assert ticker.history_calls == []


def test_get_bars_raises_on_empty_dataframe() -> None:
    ticker = MockTicker("AAPL", history_df=pd.DataFrame())
    provider = _provider_with_ticker(ticker)

    with pytest.raises(ValueError, match="No bar data available for symbol 'AAPL'"):
        provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 5)


def test_get_bars_converts_rows_to_market_bar() -> None:
    history = _make_history_df(rows=2)
    ticker = MockTicker("MSFT", history_df=history)
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("MSFT"), TimeFrame.H1, 2)

    assert len(bars) == 2
    assert all(isinstance(bar, MarketBar) for bar in bars)
    assert bars[0]["symbol"] == Symbol("MSFT")
    assert bars[0]["timeframe"] == TimeFrame.H1.value
    assert set(bars[0].keys()) == {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
        "timeframe",
    }


@pytest.mark.parametrize(
    ("timeframe", "expected_interval"),
    [
        (TimeFrame.M1, "1m"),
        (TimeFrame.M5, "5m"),
        (TimeFrame.M15, "15m"),
        (TimeFrame.H1, "1h"),
        (TimeFrame.H4, "1h"),
        (TimeFrame.D1, "1d"),
        (TimeFrame.W1, "1wk"),
    ],
)
def test_timeframe_mapping_to_yfinance_interval(
    timeframe: TimeFrame,
    expected_interval: str,
) -> None:
    assert _timeframe_to_yfinance_interval(timeframe) == expected_interval


def test_get_bars_uses_mapped_interval_for_timeframe() -> None:
    history = _make_history_df(rows=1)
    ticker = MockTicker("AAPL", history_df=history)
    provider = _provider_with_ticker(ticker)

    provider.get_bars(Symbol("AAPL"), TimeFrame.M15, 1)

    assert ticker.history_calls[0]["interval"] == "15m"


def test_get_bars_handles_naive_timestamps_as_utc() -> None:
    naive_history = _make_history_df(rows=1, tz=None)
    ticker = MockTicker("AAPL", history_df=naive_history)
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 1)

    assert bars[0]["timestamp"].tzinfo == timezone.utc


def test_get_bars_converts_aware_timestamps_to_utc() -> None:
    eastern = ZoneInfo("America/New_York")
    aware_history = _make_history_df(
        rows=1,
        start=datetime(2026, 1, 1, 9, 0, tzinfo=eastern),
        tz=eastern,
    )
    ticker = MockTicker("AAPL", history_df=aware_history)
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 1)

    assert bars[0]["timestamp"].tzinfo == timezone.utc
    assert bars[0]["timestamp"].hour == 14


def test_ensure_utc_normalizes_naive_and_aware_datetimes() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    eastern = ZoneInfo("America/New_York")
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=eastern)

    assert _ensure_utc(naive).tzinfo == timezone.utc
    assert _ensure_utc(aware).tzinfo == timezone.utc
    assert _ensure_utc(aware).hour == 17


def test_get_bars_quantizes_decimal_fields() -> None:
    history = pd.DataFrame(
        {
            "Open": [100.126],
            "High": [101.994],
            "Low": [99.001],
            "Close": [100.555],
            "Volume": [1234.0],
        },
        index=pd.DatetimeIndex([datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)]),
    )
    ticker = MockTicker("AAPL", history_df=history)
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 1)
    bar = bars[0]

    assert bar["open"] == Decimal("100.13")
    assert bar["high"] == Decimal("101.99")
    assert bar["low"] == Decimal("99.00")
    assert bar["close"] == Decimal("100.56")


def test_row_to_market_bar_converts_volume_nan_to_zero() -> None:
    row = pd.Series(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.5,
            "Volume": float("nan"),
        }
    )

    bar = _row_to_market_bar(
        datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        row,
        Symbol("AAPL"),
        TimeFrame.H1,
    )

    assert bar["volume"] == Decimal("0")


def test_get_bars_respects_limit() -> None:
    history = _make_history_df(rows=5)
    ticker = MockTicker("AAPL", history_df=history)
    provider = _provider_with_ticker(ticker)

    bars = provider.get_bars(Symbol("AAPL"), TimeFrame.H1, 3)

    assert len(bars) == 3
