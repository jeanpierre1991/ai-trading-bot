"""Market data ingestion and caching."""

from __future__ import annotations

from market_data.freshness import (
    FreshnessResult,
    evaluate_bar_freshness,
    evaluate_last_bar_freshness,
    normalize_bar_timestamp,
    resolve_max_age_seconds,
    timeframe_seconds,
    utc_now,
)
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
    "FreshnessResult",
    "HistoricalMarketDataProvider",
    "HistoricalRuntimeMarketData",
    "MarketDataModule",
    "MarketDataProvider",
    "MockMarketDataProvider",
    "YahooFinanceProvider",
    "MODULE_CLASS",
    "evaluate_bar_freshness",
    "evaluate_last_bar_freshness",
    "normalize_bar_timestamp",
    "resolve_max_age_seconds",
    "timeframe_seconds",
    "utc_now",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(MarketDataModule)
