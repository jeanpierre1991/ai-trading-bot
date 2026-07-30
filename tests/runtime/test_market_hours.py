"""M11.3 market-hours gate + session-aware freshness in run_once."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionStatus
from broker_interface.quotes import ClosedBarQuoteSource
from config.settings import Settings
from core.types import MarketBar, SignalAction
from market_data.calendars.us_equity_xnys import UsEquityXnysCalendar
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_NY = ZoneInfo("America/New_York")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(timezone.utc)


def _bar(*, timestamp: datetime, close: Decimal = Decimal("100")) -> MarketBar:
    return MarketBar(
        timestamp=timestamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol="AAPL",
        timeframe="1h",
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "paper",
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
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _runtime(
    *,
    bars: list[MarketBar],
    now: datetime,
    settings: Settings | None = None,
    executor: object | None = None,
    action: SignalAction = SignalAction.BUY,
) -> BasicTradingRuntime:
    settings = settings or _settings()
    md = MagicMock()
    md.get_bars.return_value = bars
    strategy = MagicMock()
    close = Decimal("100")
    if bars:
        close_attr = getattr(bars[-1], "close", None)
        if close_attr is not None and not callable(close_attr):
            close = Decimal(str(close_attr))
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=action,
        confidence=0.9,
        strategy_name="ema_crossover",
        price=close,
    )
    return BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=executor if executor is not None else DryRunExecutor(),
        clock=lambda: now,
        session_calendar=UsEquityXnysCalendar(),
    )


def test_rth_fresh_bar_allows_cycle() -> None:
    now = _utc(2026, 7, 15, 11, 0)
    bar = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    runtime = _runtime(bars=[bar], now=now)
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is True
    assert result.execution is not None


def test_weekend_friday_bar_not_wall_clock_stale_when_allow() -> None:
    """Sunday wall clock would make Friday bar look stale without session T_ref."""
    now = _utc(2026, 7, 19, 12, 0)  # Sunday
    friday_bar = _bar(timestamp=_utc(2026, 7, 17, 15, 0))
    runtime = _runtime(bars=[friday_bar], now=now, settings=_settings(market_hours_policy="allow"))
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is True
    runtime.strategy_engine.evaluate.assert_called_once()


def test_reject_policy_aborts_weekend_before_strategy() -> None:
    now = _utc(2026, 7, 19, 12, 0)
    friday_bar = _bar(timestamp=_utc(2026, 7, 17, 15, 0))
    runtime = _runtime(
        bars=[friday_bar],
        now=now,
        settings=_settings(market_hours_policy="reject"),
    )
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert result.stage_reached == "market_hours"
    assert "reject" in (result.aborted_reason or "").lower()
    runtime.strategy_engine.evaluate.assert_not_called()


def test_stale_during_rth_still_fails() -> None:
    now = _utc(2026, 7, 15, 15, 0)
    # 5 hours old during RTH with ~2h+slack max age
    old = _bar(timestamp=_utc(2026, 7, 15, 10, 0))
    runtime = _runtime(bars=[old], now=now)
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert result.stage_reached == "market_data"
    assert "stale" in (result.aborted_reason or "")


def test_thursday_bar_after_friday_close_is_stale_on_weekend() -> None:
    now = _utc(2026, 7, 19, 12, 0)  # Sunday; last close Friday 16:00
    thursday = _bar(timestamp=_utc(2026, 7, 16, 12, 0))
    runtime = _runtime(bars=[thursday], now=now)
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert "stale" in (result.aborted_reason or "")


def test_context_disables_market_hours() -> None:
    now = _utc(2026, 7, 19, 12, 0)
    friday_bar = _bar(timestamp=_utc(2026, 7, 17, 15, 0))
    runtime = _runtime(
        bars=[friday_bar],
        now=now,
        settings=_settings(market_hours_policy="reject"),
    )
    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            daily_pnl_pct=Decimal("0"),
            enforce_market_hours=False,
        )
    )
    assert result.success is True


def test_holiday_reject_aborts() -> None:
    now = _utc(2025, 7, 4, 12, 0)
    # Prior session Jul 3 early close — bar near early close
    bar = _bar(timestamp=_utc(2025, 7, 3, 12, 0))
    runtime = _runtime(
        bars=[bar],
        now=now,
        settings=_settings(market_hours_policy="reject"),
    )
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert result.stage_reached == "market_hours"


def test_quote_source_reject_off_hours_no_static_fallback() -> None:
    now = _utc(2026, 7, 19, 12, 0)
    md = MagicMock()
    md.get_bars.return_value = [_bar(timestamp=_utc(2026, 7, 17, 15, 0), close=Decimal("155"))]
    source = ClosedBarQuoteSource(
        md,
        session_calendar=UsEquityXnysCalendar(),
        market_hours_enabled=True,
        market_hours_policy="reject",
        freshness_enabled=True,
        clock=lambda: now,
    )
    broker = PaperBroker(buying_power=Decimal("100000"), quote_source=source)
    broker.connect()
    before = broker.get_status().buying_power
    from broker_interface.orders import BrokerOrderRequest
    from core.types import OrderType, Side, Symbol

    result = broker.place_order(
        BrokerOrderRequest(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
        )
    )
    assert result.status is ExecutionStatus.REJECTED
    assert result.fill_price == Decimal("0")
    assert result.fill_price != Decimal("190.25")
    assert broker.get_status().buying_power == before


def test_early_close_transition() -> None:
    # 2025-07-03 early close 13:00 — at 12:59 open, at 13:00 closed
    bar = _bar(timestamp=_utc(2025, 7, 3, 12, 0))
    open_rt = _runtime(
        bars=[bar],
        now=_utc(2025, 7, 3, 12, 59),
        settings=_settings(market_hours_policy="reject"),
    )
    assert open_rt.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))).success is True

    closed_rt = _runtime(
        bars=[bar],
        now=_utc(2025, 7, 3, 13, 0),
        settings=_settings(market_hours_policy="reject"),
    )
    closed = closed_rt.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert closed.success is False
    assert closed.stage_reached == "market_hours"
