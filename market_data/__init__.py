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
from market_data.session_calendar import (
    MarketHoursPolicy,
    SessionSnapshot,
    SessionState,
    freshness_reference_now,
    is_trading_permitted,
)
from market_data.yahoo_provider import YahooFinanceProvider
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar, build_session_calendar

MODULE_CLASS = MarketDataModule

__all__ = [
    "MAX_HISTORICAL_BARS",
    "FreshnessResult",
    "HistoricalMarketDataProvider",
    "HistoricalRuntimeMarketData",
    "MarketDataModule",
    "MarketDataProvider",
    "MarketHoursPolicy",
    "MockMarketDataProvider",
    "SessionSnapshot",
    "SessionState",
    "UsEquityXnysCalendar",
    "YahooFinanceProvider",
    "MODULE_CLASS",
    "build_session_calendar",
    "evaluate_bar_freshness",
    "evaluate_last_bar_freshness",
    "freshness_reference_now",
    "is_trading_permitted",
    "normalize_bar_timestamp",
    "resolve_max_age_seconds",
    "timeframe_seconds",
    "utc_now",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(MarketDataModule)
