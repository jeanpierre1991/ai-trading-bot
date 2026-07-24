"""Tests for optional DryRunExecutor integration in BasicTradingRuntime."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.execution import ExecutionResult, ExecutionStatus
from config.settings import Settings
from core.types import OrderType, PositionId, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio, Position
from risk_manager.basic import BasicRiskManager
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


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


def _long_position(*, quantity: Decimal = Decimal("100")) -> Position:
    return Position(
        position_id=PositionId("pos-aapl"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=quantity,
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
    )


def _runtime(
    *,
    signal: StrategySignal,
    portfolio: Portfolio | None = None,
    executor: object | None = None,
) -> tuple[BasicTradingRuntime, MagicMock, Portfolio]:
    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = portfolio or Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = signal
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,  # type: ignore[arg-type]
    )
    return runtime, strategy_engine, portfolio


def test_approved_intent_executed_in_dry_run() -> None:
    executor = MagicMock(wraps=DryRunExecutor())
    runtime, _, portfolio = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=executor,
    )
    cash_before = portfolio.cash

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert isinstance(result.execution, ExecutionResult)
    assert result.execution.status is ExecutionStatus.FILLED
    assert "Dry-run" in result.execution.message
    assert result.order is None
    assert portfolio.cash < cash_before
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions
    executor.execute.assert_called_once_with(result.intent)


def test_hold_does_not_call_executor() -> None:
    executor = MagicMock(wraps=DryRunExecutor())
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
        executor=executor,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.intent is None
    assert result.execution is None
    executor.execute.assert_not_called()


def test_risk_rejection_does_not_call_executor() -> None:
    executor = MagicMock(wraps=DryRunExecutor())
    portfolio = Portfolio(cash=Decimal("0"))
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        executor=executor,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.execution is None
    executor.execute.assert_not_called()


def test_runtime_without_executor_keeps_existing_behavior() -> None:
    runtime, _, portfolio = _runtime(signal=_signal(action=SignalAction.BUY))
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert runtime.executor is None
    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert result.execution is None
    assert result.portfolio_snapshot == before


def test_executor_called_exactly_once() -> None:
    executor = MagicMock(wraps=DryRunExecutor())
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=executor,
    )

    runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert executor.execute.call_count == 1


def test_execution_result_preserved_on_pipeline_result() -> None:
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.execution is not None
    assert result.execution.symbol == Symbol("AAPL")
    assert result.execution.side is Side.BUY
    assert result.execution.requested_quantity == result.intent.quantity  # type: ignore[union-attr]
    assert result.execution.filled_quantity == result.intent.quantity  # type: ignore[union-attr]


def test_invalid_intent_rejected_by_dry_run_is_controlled() -> None:
    from runtime.executor import OrderExecutor
    from runtime.models import TradeIntent

    class RejectingExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            if intent is None:
                return DryRunExecutor().execute(None)
            bad = TradeIntent(
                symbol=intent.symbol,
                side=intent.side,
                order_type=OrderType.LIMIT,
                quantity=intent.quantity,
                limit_price=Decimal("100"),
                strategy_name=intent.strategy_name,
                signal_confidence=intent.signal_confidence,
                max_position_value=intent.max_position_value,
                reason=intent.reason,
            )
            return DryRunExecutor().execute(bad)

    runtime, _, portfolio = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=RejectingExecutor(),
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.intent is not None
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert result.aborted_reason == result.execution.message
    assert "Unsupported order_type" in (result.aborted_reason or "")
    assert portfolio.summary() == before

def test_sell_filled_books_fill_into_portfolio() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.positions["AAPL"] = _long_position(quantity=Decimal("100"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    cash_before = portfolio.cash
    qty_before = portfolio.positions["AAPL"].quantity
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.SELL),
        portfolio=portfolio,
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    portfolio.apply_fill.assert_called_once()
    assert portfolio.cash > cash_before
    assert portfolio.positions["AAPL"].quantity < qty_before


def test_no_network_or_broker_calls() -> None:
    broker = MagicMock()
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=DryRunExecutor(),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert not hasattr(runtime, "broker")
    assert not hasattr(runtime, "_broker")
    broker.connect.assert_not_called()
    broker.place_order.assert_not_called()


def test_dry_run_result_deterministic_across_cycles() -> None:
    runtime, _, _ = _runtime(
        signal=_signal(action=SignalAction.BUY),
        executor=DryRunExecutor(),
    )

    first = runtime.run_once(RuntimeContext(symbol="AAPL"))
    second = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert first.execution is not None
    assert second.execution is not None
    assert first.execution == second.execution
