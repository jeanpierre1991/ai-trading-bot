"""M11.2 runtime freshness gate in BasicTradingRuntime.run_once."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionStatus
from broker_interface.quotes import ClosedBarQuoteSource
from config.settings import Settings
from core.types import MarketBar, SignalAction
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_FIXED_NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


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
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _runtime(
    *,
    bars: list[MarketBar],
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
        last = bars[-1]
        close_attr = getattr(last, "close", None)
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
        clock=lambda: _FIXED_NOW,
    )


def test_fresh_bar_allows_strategy_and_dry_run_fill() -> None:
    bar = _bar(timestamp=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc))
    runtime = _runtime(bars=[bar])
    strategy = runtime.strategy_engine

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    strategy.evaluate.assert_called_once()


def test_stale_bar_aborts_before_strategy() -> None:
    # age = 4h > 2*3600+120
    bar = _bar(timestamp=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc))
    runtime = _runtime(bars=[bar])
    strategy = runtime.strategy_engine

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.stage_reached == "market_data"
    assert result.aborted_reason is not None
    assert "stale" in result.aborted_reason
    assert result.signal is None
    strategy.evaluate.assert_not_called()


def test_missing_timestamp_aborts_before_strategy() -> None:
    runtime = _runtime(bars=[object()])  # type: ignore[list-item]
    strategy = runtime.strategy_engine

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.stage_reached == "market_data"
    assert "missing_timestamp" in (result.aborted_reason or "")
    strategy.evaluate.assert_not_called()


def test_context_enforce_false_skips_freshness_even_when_settings_on() -> None:
    bar = _bar(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    runtime = _runtime(bars=[bar], settings=_settings(market_data_freshness_enabled=True))

    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            daily_pnl_pct=Decimal("0"),
            enforce_market_data_freshness=False,
        )
    )

    assert result.success is True
    assert result.execution is not None


def test_settings_disabled_skips_freshness() -> None:
    bar = _bar(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    runtime = _runtime(
        bars=[bar],
        settings=_settings(market_data_freshness_enabled=False),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True


def test_stale_paper_path_rejects_fill_no_static_fallback() -> None:
    stale = _bar(
        timestamp=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        close=Decimal("155.55"),
    )
    # Runtime gate would abort first; disable runtime gate and rely on quote source.
    settings = _settings(market_data_freshness_enabled=False)
    md = MagicMock()
    md.get_bars.return_value = [stale]
    source = ClosedBarQuoteSource(
        md,
        freshness_enabled=True,
        timeframe="1h",
        bar_periods=2,
        slack_seconds=120,
        future_skew_seconds=60,
        clock=lambda: _FIXED_NOW,
    )
    paper = PaperBroker(buying_power=Decimal("100000"), quote_source=source)
    paper.connect()
    before = paper.get_status().buying_power
    strategy = MagicMock()
    strategy.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.9,
        strategy_name="ema",
        price=Decimal("155.55"),
    )
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=md,
        strategy_engine=strategy,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=BrokerOrderExecutor(paper),
        clock=lambda: _FIXED_NOW,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert result.execution.fill_price == Decimal("0")
    assert result.execution.fill_price != Decimal("190.25")
    assert paper.get_status().buying_power == before


def test_absolute_max_age_override() -> None:
    # Bar age 1800s; derived would allow ~7320; absolute 1000 → stale
    bar = _bar(timestamp=datetime(2026, 7, 13, 11, 30, tzinfo=timezone.utc))
    runtime = _runtime(
        bars=[bar],
        settings=_settings(market_data_max_age_seconds=1000),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert "stale" in (result.aborted_reason or "")
