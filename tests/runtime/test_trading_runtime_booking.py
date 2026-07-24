"""Tests for M7.2 portfolio booking after bookable executions."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from config.settings import Settings
from core.types import SignalAction
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def test_buy_filled_books_fill_into_portfolio() -> None:
    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    cash_before = portfolio.cash
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status.value == "filled"
    portfolio.apply_fill.assert_called_once()
    assert portfolio.cash < cash_before
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions
