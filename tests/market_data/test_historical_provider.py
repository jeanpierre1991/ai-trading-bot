"""M10.1 historical bar source tests (deterministic, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.exceptions import ConfigurationError
from core.types import MarketBar, Symbol, TimeFrame
from market_data.historical_provider import (
    MAX_HISTORICAL_BARS,
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)


def _bar(
    index: int,
    *,
    symbol: str = "AAPL",
    timeframe: str = "1h",
    price: Decimal | None = None,
) -> MarketBar:
    close = price if price is not None else Decimal("100") + Decimal(index)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return MarketBar(
        timestamp=stamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol=symbol,
        timeframe=timeframe,
    )


def _series(count: int, **kwargs: object) -> list[MarketBar]:
    return [_bar(i, **kwargs) for i in range(count)]  # type: ignore[arg-type]


def test_provider_rejects_empty_series() -> None:
    with pytest.raises(ConfigurationError, match="non-empty"):
        HistoricalMarketDataProvider([])


def test_provider_rejects_non_increasing_timestamps() -> None:
    bars = _series(2)
    bars[1]["timestamp"] = bars[0]["timestamp"]
    with pytest.raises(ConfigurationError, match="strictly increasing"):
        HistoricalMarketDataProvider(bars)


def test_provider_rejects_oversized_series() -> None:
    # Avoid allocating 10k+ real bars: patch via direct length check path
    # by constructing with a list that reports large length is impractical;
    # instead validate the constant and a small over-limit using a stub sequence.
    class _Huge(list):
        def __len__(self) -> int:  # type: ignore[override]
            return MAX_HISTORICAL_BARS + 1

    with pytest.raises(ConfigurationError, match="MAX_HISTORICAL_BARS"):
        HistoricalMarketDataProvider(_Huge([_bar(0)]))


def test_default_window_exposes_all_bars() -> None:
    bars = _series(5)
    provider = HistoricalMarketDataProvider(bars)
    assert provider.end_exclusive == 5
    assert provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=10) == bars
    assert provider.get_latest_price(Symbol("AAPL")) == bars[-1].close


def test_cursor_window_and_advance_are_bounded() -> None:
    bars = _series(5)
    provider = HistoricalMarketDataProvider(bars, initial_end_exclusive=2)

    assert provider.visible_count == 2
    assert provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=10) == bars[:2]
    assert provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=1) == [bars[1]]

    provider.advance(1)
    assert provider.end_exclusive == 3
    assert provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=2) == bars[1:3]

    provider.advance(2)
    assert provider.end_exclusive == 5
    assert provider.remaining == 0
    assert provider.can_advance() is False

    with pytest.raises(ConfigurationError, match="remaining"):
        provider.advance(1)


def test_reset_hides_bars() -> None:
    provider = HistoricalMarketDataProvider(_series(4), initial_end_exclusive=4)
    provider.reset(end_exclusive=0)
    assert provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=10) == []
    with pytest.raises(ConfigurationError, match="no visible"):
        provider.get_latest_price(Symbol("AAPL"))


def test_symbol_and_timeframe_mismatch_rejected() -> None:
    provider = HistoricalMarketDataProvider(_series(3))
    with pytest.raises(ConfigurationError, match="symbol"):
        provider.get_bars(Symbol("MSFT"), TimeFrame.H1, limit=1)
    with pytest.raises(ConfigurationError, match="timeframe"):
        provider.get_bars(Symbol("AAPL"), TimeFrame.D1, limit=1)


def test_invalid_limit_rejected() -> None:
    provider = HistoricalMarketDataProvider(_series(2))
    with pytest.raises(ConfigurationError, match="limit"):
        provider.get_bars(Symbol("AAPL"), TimeFrame.H1, limit=0)


def test_runtime_adapter_matches_runtime_get_bars_signature() -> None:
    bars = _series(6)
    provider = HistoricalMarketDataProvider(bars, initial_end_exclusive=3)
    adapter = HistoricalRuntimeMarketData(provider)

    got = adapter.get_bars(symbol="AAPL", limit=2)
    assert got == bars[1:3]
    assert adapter.get_price("AAPL") == bars[2].close

    provider.advance(1)
    assert adapter.get_bars(limit=10) == bars[:4]


def test_historical_provider_has_no_network_imports() -> None:
    import market_data.historical_provider as mod

    assert not hasattr(mod, "yf")
    assert "yfinance" not in mod.__dict__
    assert "requests" not in mod.__dict__
    assert "urllib" not in mod.__dict__
