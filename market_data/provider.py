"""Market data provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from random import Random

from core.types import MarketBar, Symbol, TimeFrame


class MarketDataProvider(ABC):
    @abstractmethod
    def get_latest_price(self, symbol: Symbol) -> Decimal:
        ...

    @abstractmethod
    def get_bars(self, symbol: Symbol, timeframe: TimeFrame, limit: int) -> list[MarketBar]:
        ...


class MockMarketDataProvider(MarketDataProvider):
    """Deterministic mock provider for development and testing."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = Random(seed)
        self._base_prices: dict[str, Decimal] = {
            "AAPL": Decimal("190.00"),
            "MSFT": Decimal("420.00"),
            "GOOGL": Decimal("175.00"),
            "SPY": Decimal("520.00"),
        }

    def _base_price(self, symbol: Symbol) -> Decimal:
        return self._base_prices.get(symbol, Decimal("100.00"))

    def get_latest_price(self, symbol: Symbol) -> Decimal:
        base = self._base_price(symbol)
        jitter = Decimal(str(self._rng.uniform(-0.5, 0.5)))
        return (base + jitter).quantize(Decimal("0.01"))

    def get_bars(self, symbol: Symbol, timeframe: TimeFrame, limit: int) -> list[MarketBar]:
        bars: list[MarketBar] = []
        price = self._base_price(symbol)
        now = datetime.now(timezone.utc)

        interval_minutes = {
            TimeFrame.M1: 1,
            TimeFrame.M5: 5,
            TimeFrame.M15: 15,
            TimeFrame.H1: 60,
            TimeFrame.H4: 240,
            TimeFrame.D1: 1440,
            TimeFrame.W1: 10080,
        }.get(timeframe, 60)

        for i in range(limit):
            delta = Decimal(str(self._rng.uniform(-1.5, 1.5)))
            open_price = price
            close_price = (price + delta).quantize(Decimal("0.01"))
            high_price = max(open_price, close_price) + Decimal(str(self._rng.uniform(0, 1)))
            low_price = min(open_price, close_price) - Decimal(str(self._rng.uniform(0, 1)))
            volume = Decimal(str(self._rng.randint(10000, 500000)))

            timestamp = now - timedelta(minutes=interval_minutes * (limit - i))
            bars.append(
                MarketBar(
                    timestamp=timestamp,
                    open=open_price,
                    high=high_price.quantize(Decimal("0.01")),
                    low=low_price.quantize(Decimal("0.01")),
                    close=close_price,
                    volume=volume,
                    symbol=symbol,
                    timeframe=timeframe.value,
                )
            )
            price = close_price

        return bars
