"""Tests for M7.2/M7.3 portfolio booking after bookable executions."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from config.settings import Settings
from core.types import OrderId, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _buy_signal() -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )


def _runtime_with_executor(
    *,
    executor: OrderExecutor,
    portfolio: Portfolio,
) -> BasicTradingRuntime:
    settings = Settings(max_position_size_pct=Decimal("0.05"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = _buy_signal()
    return BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,
    )


def test_buy_filled_books_fill_into_portfolio() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    cash_before = portfolio.cash
    runtime = _runtime_with_executor(
        executor=DryRunExecutor(),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status.value == "filled"
    portfolio.apply_fill.assert_called_once()
    assert portfolio.cash < cash_before
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions
    assert result.portfolio_snapshot is not None
    assert result.portfolio_snapshot == portfolio.summary()


def test_dry_run_and_paper_share_buy_booking_runtime_contract() -> None:
    """Same BUY path books through DryRun and Paper with the same Runtime contract."""
    paper = PaperBroker(buying_power=Decimal("100000"))
    paper.connect()

    cases: list[tuple[str, OrderExecutor, Portfolio]] = [
        ("dry_run", DryRunExecutor(), Portfolio(cash=Decimal("100000"))),
        ("paper", BrokerOrderExecutor(paper), Portfolio(cash=Decimal("100000"))),
    ]

    for label, executor, portfolio in cases:
        cash_before = portfolio.cash
        runtime = _runtime_with_executor(executor=executor, portfolio=portfolio)

        result = runtime.run_once(RuntimeContext(symbol="AAPL"))

        assert result.success is True, label
        assert result.stage_reached == "portfolio", label
        assert result.execution is not None, label
        assert result.execution.status is ExecutionStatus.FILLED, label
        assert result.portfolio_snapshot is not None, label
        assert result.portfolio_snapshot == portfolio.summary(), label
        assert portfolio.cash < cash_before, label
        assert portfolio.position_count == 1, label
        assert "AAPL" in portfolio.positions, label


def test_bookable_sell_without_position_returns_controlled_apply_fill_failure() -> None:
    """FILLED SELL from executor must not crash when portfolio has no position."""

    class ForcedSellFilledExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            return ExecutionResult(
                order_id=OrderId("forced-sell"),
                symbol=Symbol("AAPL"),
                side=Side.SELL,
                requested_quantity=Decimal("5"),
                filled_quantity=Decimal("5"),
                fill_price=Decimal("100"),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message="Forced filled sell",
            )

    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    # BUY path reaches execution; stub executor returns an incompatible FILLED SELL.
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
        executor=ForcedSellFilledExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.stage_reached == "portfolio"
    assert result.aborted_reason is not None
    assert "apply_fill failed" in result.aborted_reason
    assert "no open position" in result.aborted_reason
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before


def test_invalid_bookable_execution_returns_controlled_execution_to_fill_failure() -> None:
    """FILLED with invalid qty must fail in the mapper before apply_fill."""

    class InvalidFilledExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            return ExecutionResult(
                order_id=OrderId("invalid-filled"),
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                requested_quantity=Decimal("10"),
                filled_quantity=Decimal("0"),
                fill_price=Decimal("100"),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message="Invalid filled payload",
            )

    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    before = portfolio.summary()
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
        executor=InvalidFilledExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.stage_reached == "execution"
    assert result.aborted_reason is not None
    assert "execution_to_fill failed" in result.aborted_reason
    portfolio.apply_fill.assert_not_called()
    assert portfolio.summary() == before
    assert result.portfolio_snapshot == before
