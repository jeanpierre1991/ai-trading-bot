"""M11.4 supervised paper path — mocked Yahoo → runtime (no network).

Proves Yahoo-shaped market data can drive dry-run and paper cycles through
M11.1–M11.3 gates. CI must never call live yfinance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionStatus
from broker_interface.quotes import ClosedBarQuoteSource
from config.settings import Settings
from core.types import SignalAction, Symbol, TimeFrame
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar
from market_data.yahoo_provider import YahooFinanceProvider
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_NY = ZoneInfo("America/New_York")
_CLOSE = Decimal("187.6543")


class _MockTicker:
    def __init__(self, history_df: pd.DataFrame) -> None:
        self.fast_info: dict[str, Any] = {"last_price": float(_CLOSE)}
        self.history_df = history_df
        self.history_calls: list[dict[str, str]] = []

    def history(self, period: str, interval: str) -> pd.DataFrame:
        self.history_calls.append({"period": period, "interval": interval})
        return self.history_df


class _YahooRuntimeMarketData:
    """Adapts YahooFinanceProvider to runtime ``get_bars(symbol=, limit=)``."""

    def __init__(self, provider: YahooFinanceProvider, *, timeframe: TimeFrame) -> None:
        self._provider = provider
        self._timeframe = timeframe

    def get_bars(self, symbol: str | None = None, limit: int = 100) -> list:
        return self._provider.get_bars(Symbol(symbol or "AAPL"), self._timeframe, limit)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(timezone.utc)


def _history_df(*, closes: list[float], start_local: datetime) -> pd.DataFrame:
    timestamps = [start_local + timedelta(hours=i) for i in range(len(closes))]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=pd.DatetimeIndex(timestamps, tz=_NY),
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "paper",
        "market_data_provider": "yahoo",  # documented path; provider is mocked
        "max_position_size_pct": Decimal("0.05"),
        "default_timeframe": "1h",
        "market_data_freshness_enabled": True,
        "market_data_freshness_bar_periods": 2,
        "market_data_freshness_slack_seconds": 120,
        "market_data_future_skew_seconds": 60,
        "market_hours_enabled": True,
        "market_hours_policy": "allow",
        "market_hours_calendar": "xnys",
        "market_hours_timezone": "America/New_York",
        "alerts_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _yahoo_md(*, history: pd.DataFrame) -> tuple[_YahooRuntimeMarketData, _MockTicker]:
    ticker = _MockTicker(history)
    provider = YahooFinanceProvider(ticker_factory=lambda _symbol: ticker)
    return _YahooRuntimeMarketData(provider, timeframe=TimeFrame.H1), ticker


def _strategy(close: Decimal) -> MagicMock:
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=close,
    )
    return strategy


def test_mocked_yahoo_dry_run_cycle_succeeds_in_rth() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    history = _history_df(
        closes=[186.0, 186.5, float(_CLOSE)],
        start_local=datetime(2026, 7, 15, 8, 0, tzinfo=_NY),
    )
    md, ticker = _yahoo_md(history=history)
    settings = _settings()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=_strategy(_CLOSE),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert ticker.history_calls  # Yahoo provider path exercised (mocked)
    assert result.stage_reached == "portfolio"


def test_mocked_yahoo_paper_fill_matches_last_closed_bar_close() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    history = _history_df(
        closes=[186.0, 186.5, float(_CLOSE)],
        start_local=datetime(2026, 7, 15, 8, 0, tzinfo=_NY),
    )
    md, _ticker = _yahoo_md(history=history)
    settings = _settings()
    calendar = UsEquityXnysCalendar()
    quote_source = ClosedBarQuoteSource(
        md,
        freshness_enabled=True,
        timeframe="1h",
        bar_periods=2,
        slack_seconds=120,
        future_skew_seconds=60,
        clock=lambda: now,
        session_calendar=calendar,
        market_hours_enabled=True,
        market_hours_policy="allow",
    )
    paper = PaperBroker(buying_power=Decimal("100000"), quote_source=quote_source)
    paper.connect()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=_strategy(_CLOSE),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=BrokerOrderExecutor(paper),
        clock=lambda: now,
        session_calendar=calendar,
    )

    bars = md.get_bars(symbol="AAPL", limit=3)
    last_close = bars[-1].close.quantize(Decimal("0.0001"))

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.execution.fill_price == last_close
    assert result.execution.fill_price != Decimal("190.25")  # no static fallback


def test_mocked_yahoo_empty_history_aborts_without_fill() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    md, _ticker = _yahoo_md(history=pd.DataFrame())
    settings = _settings()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=_strategy(_CLOSE),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.stage_reached == "market_data"
    assert result.execution is None
    runtime.strategy_engine.evaluate.assert_not_called()


def test_mocked_yahoo_stale_during_rth_aborts_before_strategy() -> None:
    now = _utc(2026, 7, 15, 15, 0)
    # Bars from 08:00–10:00 local; last bar ~5h old vs wall clock in RTH.
    history = _history_df(
        closes=[186.0, 186.5, float(_CLOSE)],
        start_local=datetime(2026, 7, 15, 8, 0, tzinfo=_NY),
    )
    md, _ticker = _yahoo_md(history=history)
    settings = _settings()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=_strategy(_CLOSE),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.stage_reached == "market_data"
    assert "stale" in (result.aborted_reason or "")
    runtime.strategy_engine.evaluate.assert_not_called()
