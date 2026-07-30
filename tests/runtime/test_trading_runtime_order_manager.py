"""M8.4 optional OrderManager wiring in BasicTradingRuntime."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from config.settings import Settings
from core.types import OrderId, Side, SignalAction, Symbol, TradingMode
from order_manager.manager import OrderManager, OrderState
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _signal(
    *,
    action: SignalAction = SignalAction.BUY,
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


def _settings() -> Settings:
    return Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )


def _runtime(
    *,
    signal: StrategySignal,
    portfolio: Portfolio | None = None,
    executor: OrderExecutor | None = None,
    order_manager: OrderManager | None = None,
    settings: Settings | None = None,
) -> BasicTradingRuntime:
    settings = settings or _settings()
    portfolio = portfolio or Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = signal
    return BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,
        order_manager=order_manager,
    )


def test_without_order_manager_keeps_post_m83_behavior() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    runtime = _runtime(signal=_signal(), portfolio=portfolio, executor=DryRunExecutor())

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.order is None
    assert portfolio.position_count == 1


def test_buy_filled_with_order_manager_sets_order_filled() -> None:
    order_manager = OrderManager()
    portfolio = Portfolio(cash=Decimal("100000"))
    runtime = _runtime(
        signal=_signal(),
        portfolio=portfolio,
        executor=DryRunExecutor(),
        order_manager=order_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.order is not None
    assert result.order.state is OrderState.FILLED
    assert result.order.symbol == Symbol("AAPL")
    assert result.order.side is Side.BUY
    assert order_manager.order_count == 1
    assert order_manager.get_order(str(result.order.order_id)) is result.order
    # Distinct layer IDs are allowed in M8.4.
    assert result.order.order_id != result.execution.order_id
    assert portfolio.position_count == 1


def test_rejected_with_order_manager_sets_order_rejected() -> None:
    order_manager = OrderManager()
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    paper = PaperBroker(buying_power=Decimal("100000"))
    # Disconnected paper broker rejects.
    runtime = _runtime(
        signal=_signal(),
        portfolio=portfolio,
        executor=BrokerOrderExecutor(paper),
        order_manager=order_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert result.order is not None
    assert result.order.state is OrderState.REJECTED
    assert order_manager.order_count == 1
    assert portfolio.summary() == before


def test_intent_only_with_order_manager_leaves_order_pending() -> None:
    order_manager = OrderManager()
    runtime = _runtime(
        signal=_signal(),
        executor=None,
        order_manager=order_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert result.execution is None
    assert result.order is not None
    assert result.order.state is OrderState.PENDING
    assert order_manager.order_count == 1


def test_risk_abort_with_order_manager_does_not_create_order() -> None:
    order_manager = OrderManager()
    executor = MagicMock()
    runtime = _runtime(
        signal=_signal(),
        executor=executor,
        order_manager=order_manager,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.order is None
    assert order_manager.order_count == 0
    executor.execute.assert_not_called()


def test_mode_abort_with_order_manager_does_not_create_order() -> None:
    order_manager = OrderManager()
    runtime = _runtime(
        signal=_signal(),
        executor=DryRunExecutor(),
        order_manager=order_manager,
    )

    result = runtime.run_once(
        RuntimeContext(
            symbol="AAPL",
            mode=TradingMode.LIVE,
            daily_pnl_pct=Decimal("0"),
        ),
    )

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.order is None
    assert order_manager.order_count == 0


def test_hold_with_order_manager_does_not_create_order() -> None:
    order_manager = OrderManager()
    runtime = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
        executor=DryRunExecutor(),
        order_manager=order_manager,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "risk"
    assert result.order is None
    assert order_manager.order_count == 0


def test_filled_execution_with_apply_fill_failure_keeps_order_filled() -> None:
    """Order state follows ExecutionStatus, not booking success."""

    class ForcedSellFilledExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            assert intent is not None
            return ExecutionResult(
                order_id=OrderId("forced-sell"),
                symbol=Symbol("AAPL"),
                side=Side.SELL,
                requested_quantity=Decimal("5"),
                filled_quantity=Decimal("5"),
                fill_price=Decimal("100"),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message="Forced filled sell without position",
            )

    order_manager = OrderManager()
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    # BUY path reaches execution; stub returns incompatible FILLED SELL.
    runtime = _runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
        executor=ForcedSellFilledExecutor(),
        order_manager=order_manager,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "portfolio"
    assert result.aborted_reason is not None
    assert "apply_fill failed" in result.aborted_reason
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.order is not None
    assert result.order.state is OrderState.FILLED
    assert order_manager.order_count == 1
    assert portfolio.summary() == before
