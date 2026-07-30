"""Market data ingestion and caching."""

from __future__ import annotations

from market_data.historical_provider import (
    MAX_HISTORICAL_BARS,
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from market_data.module import MarketDataModule
from market_data.provider import MarketDataProvider, MockMarketDataProvider
from market_data.yahoo_provider import YahooFinanceProvider

MODULE_CLASS = MarketDataModule

__all__ = [
    "MAX_HISTORICAL_BARS",
    "HistoricalMarketDataProvider",
    "HistoricalRuntimeMarketData",
    "MarketDataModule",
    "MarketDataProvider",
    "MockMarketDataProvider",
    "YahooFinanceProvider",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(MarketDataModule)
