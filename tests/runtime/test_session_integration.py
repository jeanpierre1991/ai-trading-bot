"""M9.2 SessionRunner integration with BasicTradingRuntime / factory (no network)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionStatus
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import MarketBar, SignalAction
from order_manager.manager import OrderManager, OrderState
from portfolio_manager.portfolio import Portfolio
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.factory import create_trading_runtime
from runtime.session import SessionConfig, SessionRunner
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal
from datetime import datetime, timezone


def _signal(
    *,
    action: SignalAction,
    price: Decimal = Decimal("100"),
    symbol: str = "AAPL",
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=action,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=price,
    )


def _mock_market_data(*, close: Decimal = Decimal("100")) -> MagicMock:
    """Bars expose a closed price so M11.1 ClosedBarQuoteSource can fill paper orders."""
    market_data = MagicMock()
    market_data.get_bars.return_value = [
        MarketBar(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("1000"),
            symbol="AAPL",
            timeframe="1h",
        )
    ]
    return market_data


def _mock_strategy(*signals: StrategySignal) -> MagicMock:
    strategy_engine = MagicMock()
    strategy_engine.evaluate.side_effect = list(signals)
    return strategy_engine


def _paper_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": "paper",
        "market_data_provider": "mock",
        "broker_name": "paper",
        "max_position_size_pct": Decimal("0.05"),
        "max_daily_loss_pct": Decimal("0.50"),  # loose so MTM tests are not blocked
        "alerts_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_multi_cycle_dry_run_session_with_real_runtime() -> None:
    settings = _paper_settings()
    portfolio = Portfolio(cash=Decimal("100000"))
    order_manager = OrderManager()
    strategy = _mock_strategy(
        _signal(action=SignalAction.BUY),
        _signal(action=SignalAction.HOLD, price=Decimal("0")),
    )

    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        market_data=_mock_market_data(),
        strategy_engine=strategy,
        portfolio=portfolio,
        order_manager=order_manager,
        with_alerts=False,
    )

    assert isinstance(runtime, BasicTradingRuntime)
    assert isinstance(runtime.executor, DryRunExecutor)
    assert runtime.portfolio is portfolio
    assert runtime.order_manager is order_manager

    result = SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    assert result.cycles_executed == 2
    assert result.stopped_early is False
    assert result.results[0].success is True
    assert result.results[0].execution is not None
    assert result.results[0].execution.status is ExecutionStatus.FILLED
    assert result.results[1].success is True
    assert result.results[1].intent is None  # HOLD
    assert runtime.portfolio is portfolio
    assert runtime.order_manager is order_manager
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions


def test_runtime_portfolio_and_order_manager_persist_across_cycles() -> None:
    settings = _paper_settings()
    portfolio = Portfolio(cash=Decimal("100000"))
    order_manager = OrderManager()
    strategy = _mock_strategy(
        _signal(action=SignalAction.BUY),
        _signal(action=SignalAction.HOLD, price=Decimal("0")),
    )
    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        market_data=_mock_market_data(),
        strategy_engine=strategy,
        portfolio=portfolio,
        order_manager=order_manager,
        with_alerts=False,
    )

    SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    assert runtime.portfolio is portfolio
    assert runtime.order_manager is order_manager
    assert order_manager.order_count == 1
    order = order_manager.list_orders()[0]
    assert order.state is OrderState.FILLED


def test_paper_session_via_factory_paper_broker() -> None:
    settings = _paper_settings()
    portfolio = Portfolio(cash=Decimal("100000"))
    strategy = _mock_strategy(
        _signal(action=SignalAction.BUY, price=Decimal("190")),
        _signal(action=SignalAction.HOLD, price=Decimal("0")),
    )

    runtime = create_trading_runtime(
        settings,
        execution="paper",
        market_data=_mock_market_data(),
        strategy_engine=strategy,
        portfolio=portfolio,
        with_order_manager=True,
        with_alerts=False,
    )

    assert isinstance(runtime.executor, BrokerOrderExecutor)
    assert isinstance(runtime.executor.broker, PaperBroker)
    assert runtime.executor.broker.get_status().connected is True

    result = SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    assert result.cycles_executed == 2
    assert result.stopped_early is False
    assert result.results[0].execution is not None
    assert result.results[0].execution.status is ExecutionStatus.FILLED
    assert portfolio.position_count == 1
    assert portfolio.cash < Decimal("100000")


def test_booked_fill_equity_change_reflected_in_next_cycle_session_pnl() -> None:
    """After a booked BUY, MTM mark-down changes equity; next cycle sees session PnL."""
    settings = _paper_settings()
    portfolio = Portfolio(cash=Decimal("100000"))
    start_equity = portfolio.total_value
    strategy = _mock_strategy(
        _signal(action=SignalAction.BUY),
        _signal(action=SignalAction.HOLD, price=Decimal("0")),
    )
    runtime = create_trading_runtime(
        settings,
        execution="dry_run",
        market_data=_mock_market_data(),
        strategy_engine=strategy,
        portfolio=portfolio,
        with_order_manager=True,
        with_alerts=False,
    )

    contexts: list = []
    original_run_once = runtime.run_once

    def _tracking_run_once(context):  # type: ignore[no-untyped-def]
        contexts.append(context)
        result = original_run_once(context)
        if len(contexts) == 1 and result.success:
            # Simulate post-fill mark-to-market loss on the shared portfolio.
            position = portfolio.positions["AAPL"]
            position.current_price = position.entry_price * Decimal("0.9")
        return result

    runtime.run_once = _tracking_run_once  # type: ignore[method-assign]

    session = SessionRunner(runtime).run(SessionConfig(cycles=2, symbol="AAPL"))

    assert session.cycles_executed == 2
    assert contexts[0].daily_pnl_pct == Decimal("0")
    assert isinstance(contexts[1].daily_pnl_pct, Decimal)
    assert contexts[1].daily_pnl_pct < Decimal("0")
    assert portfolio.total_value < start_equity
    expected = (portfolio.total_value - start_equity) / start_equity
    # Second cycle PnL was computed before HOLD (after MTM from cycle 1).
    # Recompute from equity at the time of cycle 2 context:
    # After cycle 1 MTM, equity is current portfolio.total_value if unchanged by HOLD.
    assert contexts[1].daily_pnl_pct == expected


def test_live_mode_rejected_before_session_runs() -> None:
    settings = _paper_settings(trading_mode="live")

    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(
            settings,
            execution="dry_run",
            market_data=_mock_market_data(),
            strategy_engine=_mock_strategy(
                _signal(action=SignalAction.HOLD, price=Decimal("0"))
            ),
            portfolio=Portfolio(cash=Decimal("100000")),
            with_alerts=False,
        )


def test_backtest_mode_rejected_before_session_runs() -> None:
    settings = _paper_settings(trading_mode="backtest")

    with pytest.raises(ConfigurationError, match="backtest"):
        create_trading_runtime(
            settings,
            execution="dry_run",
            market_data=_mock_market_data(),
            strategy_engine=_mock_strategy(
                _signal(action=SignalAction.HOLD, price=Decimal("0"))
            ),
            portfolio=Portfolio(cash=Decimal("100000")),
            with_alerts=False,
        )
