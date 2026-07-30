"""M10.3 Option A safety/integration tests (no production wiring changes).

Validates that historical backtest remains a separate PAPER + DryRun path and
that M8/M9 factory, mode_policy, run_once, and SessionRunner guarantees hold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backtesting.commission import CommissionDryRunExecutor
from backtesting.runner import BacktestConfig, BacktestRunner
from broker_interface.broker import PaperBroker
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import MarketBar, SignalAction, TimeFrame, TradingMode
from market_data.historical_provider import (
    HistoricalMarketDataProvider,
    HistoricalRuntimeMarketData,
)
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime
from runtime.mode_policy import mode_policy_violation
from runtime.session import SessionConfig, SessionRunner
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


class _NonPaperBroker:
    """Stand-in that is not PaperBroker (live-risk)."""

    def place_order(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("live broker must never be called")


class _ScriptedStrategy:
    def __init__(self, actions: list[SignalAction]) -> None:
        self._actions = list(actions)
        self._index = 0

    def evaluate(
        self,
        bars: list[MarketBar],
        *,
        symbol: str | None = None,
        strategy_name: str | None = None,
    ) -> StrategySignal:
        action = self._actions[min(self._index, len(self._actions) - 1)]
        self._index += 1
        price = bars[-1].close if bars else Decimal("100")
        return StrategySignal(
            symbol=symbol or "AAPL",
            action=action,
            confidence=0.9,
            strategy_name=strategy_name or "scripted",
            price=price,
        )


def _bar(index: int, *, price: Decimal | None = None) -> MarketBar:
    close = price if price is not None else Decimal("100") + Decimal(index)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
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


def _series(count: int) -> list[MarketBar]:
    return [_bar(i) for i in range(count)]


def _paper_runtime(
    *,
    market_data: object,
    actions: list[SignalAction],
    executor: object,
    settings: Settings | None = None,
    portfolio: Portfolio | None = None,
) -> BasicTradingRuntime:
    settings = settings or Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.10"),
        backtest_initial_capital=Decimal("100000"),
        backtest_commission_pct=Decimal("0.001"),
    )
    portfolio = portfolio or Portfolio(cash=settings.backtest_initial_capital)
    return BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=_ScriptedStrategy(actions),
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,  # type: ignore[arg-type]
    )


def test_backtest_contexts_are_always_paper() -> None:
    provider = HistoricalMarketDataProvider(_series(6), initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=CommissionDryRunExecutor(Decimal("0")),
    )
    seen_modes: list[TradingMode] = []
    original = runtime.run_once

    def _capture(context: RuntimeContext):
        seen_modes.append(context.mode)
        return original(context)

    runtime.run_once = _capture  # type: ignore[method-assign]
    BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=2, max_cycles=3, bar_limit=50)
    )

    assert seen_modes
    assert all(mode is TradingMode.PAPER for mode in seen_modes)
    assert TradingMode.BACKTEST not in seen_modes
    assert runtime.settings.trading_mode == "paper"


def test_backtest_rejects_broker_order_executor_and_live_broker() -> None:
    provider = HistoricalMarketDataProvider(_series(4), initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    settings = Settings(trading_mode="paper", backtest_initial_capital=Decimal("100000"))

    paper_runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=BrokerOrderExecutor(PaperBroker(buying_power=Decimal("100000"))),
        settings=settings,
    )
    with pytest.raises(ConfigurationError, match="BrokerOrderExecutor"):
        BacktestRunner(paper_runtime, historical=market_data)

    live_runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=BrokerOrderExecutor(_NonPaperBroker()),  # type: ignore[arg-type]
        settings=settings,
    )
    with pytest.raises(ConfigurationError, match="BrokerOrderExecutor"):
        BacktestRunner(live_runtime, historical=market_data)


def test_commission_wrapper_cannot_wrap_broker_executor() -> None:
    with pytest.raises(ConfigurationError, match="DryRunExecutor"):
        CommissionDryRunExecutor(
            Decimal("0.001"),
            inner=BrokerOrderExecutor(PaperBroker(buying_power=Decimal("1"))),  # type: ignore[arg-type]
        )


def test_historical_backtest_through_real_runtime_path() -> None:
    prices = [Decimal("100")] * 4 + [Decimal("100"), Decimal("100")]
    bars = [_bar(i, price=prices[i]) for i in range(len(prices))]
    provider = HistoricalMarketDataProvider(bars, initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.BUY, SignalAction.CLOSE, SignalAction.HOLD],
        executor=CommissionDryRunExecutor(Decimal("0")),
    )

    result = BacktestRunner(runtime, historical=market_data).run(
        BacktestConfig(symbol="AAPL", warmup_bars=4, max_cycles=3, bar_limit=50)
    )

    assert result.cycles_executed == 3
    assert result.total_trades == 2
    assert runtime.settings.trading_mode == "paper"
    assert isinstance(runtime.executor, CommissionDryRunExecutor)
    assert isinstance(runtime.executor.inner, DryRunExecutor)


def test_paper_run_once_behavior_remains_intact() -> None:
    """Ordinary paper dry-run cycle is unchanged by M10 backtest modules."""
    market_data = MagicMock()
    market_data.get_bars.return_value = [_bar(0)]
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.BUY],
        executor=DryRunExecutor(),
    )
    cash_before = runtime.portfolio.cash

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0"))
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert runtime.portfolio.cash < cash_before
    assert "AAPL" in runtime.portfolio.positions


def test_session_runner_behavior_remains_intact() -> None:
    market_data = MagicMock()
    market_data.get_bars.return_value = [_bar(0)]
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=DryRunExecutor(),
    )
    session = SessionRunner(runtime).run(
        SessionConfig(cycles=3, symbol="AAPL", bar_limit=10)
    )

    assert session.cycles_requested == 3
    assert session.cycles_executed == 3
    assert session.stopped_early is False
    assert session.completed_all_cycles is True
    assert all(r.success for r in session.results)


def test_m8_factory_still_rejects_live_and_backtest_modes() -> None:
    deps = {
        "market_data": MagicMock(),
        "strategy_engine": MagicMock(),
        "portfolio": Portfolio(cash=Decimal("100000")),
    }
    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(
            Settings(trading_mode="live"),
            execution="dry_run",
            **deps,
        )
    with pytest.raises(ConfigurationError, match="backtest"):
        create_trading_runtime(
            Settings(trading_mode="backtest"),
            execution="dry_run",
            **deps,
        )


def test_m8_mode_policy_live_guards_remain_effective() -> None:
    live_settings = mode_policy_violation(
        settings_trading_mode="live",
        context_mode=TradingMode.PAPER,
        executor=DryRunExecutor(),
    )
    assert live_settings is not None
    assert "live" in live_settings

    backtest_settings = mode_policy_violation(
        settings_trading_mode="backtest",
        context_mode=TradingMode.PAPER,
        executor=DryRunExecutor(),
    )
    assert backtest_settings is not None
    assert "backtest" in backtest_settings

    backtest_context = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode=TradingMode.BACKTEST,
        executor=DryRunExecutor(),
    )
    assert backtest_context is not None
    assert "backtest" in backtest_context

    live_broker = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode=TradingMode.PAPER,
        executor=BrokerOrderExecutor(_NonPaperBroker()),  # type: ignore[arg-type]
    )
    assert live_broker is not None
    assert "not allowed" in live_broker


def test_run_once_rejects_live_settings_even_with_dry_run() -> None:
    market_data = MagicMock()
    market_data.get_bars.return_value = [_bar(0)]
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=DryRunExecutor(),
        settings=Settings(trading_mode="live"),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0"))
    )

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.aborted_reason is not None
    assert "live" in result.aborted_reason
    market_data.get_bars.assert_not_called()


def test_backtest_settings_mode_still_fail_closed_inside_run_once() -> None:
    """Option A: trading_mode=backtest is never enabled; run_once aborts at mode."""
    provider = HistoricalMarketDataProvider(_series(4), initial_end_exclusive=0)
    market_data = HistoricalRuntimeMarketData(provider)
    runtime = _paper_runtime(
        market_data=market_data,
        actions=[SignalAction.HOLD],
        executor=DryRunExecutor(),
        settings=Settings(trading_mode="backtest"),
    )
    # Construction is allowed for the unit test, but every cycle must abort.
    runner = BacktestRunner(runtime, historical=market_data)
    result = runner.run(
        BacktestConfig(symbol="AAPL", warmup_bars=2, max_cycles=2, bar_limit=50)
    )
    assert result.cycles_executed == 1  # fail-closed stop on first unsuccessful cycle
    assert result.total_trades == 0
