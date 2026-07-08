"""Market data module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import Symbol, TimeFrame
from market_data.provider import MarketDataProvider, MockMarketDataProvider
from market_data.yahoo_provider import YahooFinanceProvider


class MarketDataModule(BaseModule):
    """Fetches and caches market data from configured providers."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._provider: MarketDataProvider | None = None
        self._cache_hits = 0
        self._requests = 0

    @property
    def name(self) -> str:
        return "market_data"

    def _on_initialize(self) -> None:
        provider_name = self._settings.market_data_provider.lower()
        if provider_name == "mock":
            self._provider = MockMarketDataProvider()
        elif provider_name in ("yahoo", "yfinance"):
            self._provider = YahooFinanceProvider()
            self.logger.info("Using Yahoo Finance market data provider")
        else:
            self._provider = MockMarketDataProvider()
            self.logger.warning(
                "Provider '%s' not implemented; falling back to mock",
                provider_name,
            )

    def get_price(self, symbol: str | None = None) -> Decimal:
        if self._provider is None:
            raise RuntimeError("Market data provider not initialized")
        sym = Symbol(symbol or self._settings.default_symbol)
        self._requests += 1
        return self._provider.get_latest_price(sym)

    def get_bars(self, symbol: str | None = None, limit: int = 100) -> list:
        if self._provider is None:
            raise RuntimeError("Market data provider not initialized")
        sym = Symbol(symbol or self._settings.default_symbol)
        timeframe = TimeFrame(self._settings.default_timeframe)
        self._requests += 1
        return self._provider.get_bars(sym, timeframe, limit)

    def health_check(self) -> ModuleHealth:
        if self._provider is None:
            return self._unhealthy("Provider not initialized")

        try:
            price = self.get_price()
            bars = self.get_bars(limit=5)
            return self._healthy(
                message="Market data provider operational",
                provider=self._settings.market_data_provider,
                sample_price=str(price),
                bars_fetched=len(bars),
                requests=self._requests,
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
