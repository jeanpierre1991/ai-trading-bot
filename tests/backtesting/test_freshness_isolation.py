"""M11.2: BacktestRunner explicitly disables market-data freshness enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backtesting.runner import BacktestConfig, BacktestRunner
from config.settings import Settings
from core.types import MarketBar, SignalAction, TimeFrame, TradingMode
from market_data.historical_provider import (
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _bar(index: int) -> MarketBar:
    close = Decimal("100") + Decimal(index)
    stamp = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return MarketBar(
        timestamp=stamp,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        symbol="AAPL",
        timeframe=TimeFrame.H1.value,
    )


class _HoldStrategy:
    def evaluate(self, bars, *, symbol=None, strategy_name=None):  # type: ignore[no-untyped-def]
        return StrategySignal(
            symbol=symbol or "AAPL",
            action=SignalAction.HOLD,
            confidence=0.5,
            strategy_name=strategy_name or "hold",
            price=bars[-1].close,
        )


def test_backtest_runner_sets_enforce_market_data_freshness_false() -> None:
    """Historical bars are years old; wall-clock freshness must be forced off."""
    settings = Settings(
        trading_mode="paper",
        market_data_freshness_enabled=True,  # would fail without context override
        max_position_size_pct=Decimal("0.10"),
        backtest_initial_capital=Decimal("100000"),
    )
    series = [_bar(i) for i in range(8)]
    provider = HistoricalMarketDataProvider(
        series,
        symbol="AAPL",
        timeframe=TimeFrame.H1,
    )
    market_data = HistoricalRuntimeMarketData(provider)
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=_HoldStrategy(),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
    )
    seen: list[RuntimeContext] = []
    original = runtime.run_once

    def _capture(context: RuntimeContext):
        seen.append(context)
        return original(context)

    runtime.run_once = _capture  # type: ignore[method-assign]

    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=3, max_cycles=2, bar_limit=10)
    )

    assert result.cycles_executed == 2
    assert seen
    assert all(ctx.enforce_market_data_freshness is False for ctx in seen)
    assert all(ctx.enforce_market_hours is False for ctx in seen)
    assert all(ctx.mode is TradingMode.PAPER for ctx in seen)


def test_backtest_succeeds_with_settings_freshness_enabled_true() -> None:
    settings = Settings(
        trading_mode="paper",
        market_data_freshness_enabled=True,
        max_position_size_pct=Decimal("0.10"),
        backtest_initial_capital=Decimal("100000"),
    )
    series = [_bar(i) for i in range(6)]
    provider = HistoricalMarketDataProvider(
        series,
        symbol="AAPL",
        timeframe=TimeFrame.H1,
    )
    market_data = HistoricalRuntimeMarketData(provider)
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=_HoldStrategy(),
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=DryRunExecutor(),
    )

    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=3, max_cycles=2, bar_limit=10)
    )

    assert result.cycles_executed == 2
    assert result.total_trades == 0
