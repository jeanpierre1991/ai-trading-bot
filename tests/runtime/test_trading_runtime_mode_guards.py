"""M8.1 mode guards: paper / dry-run only; block live and non-paper brokers."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import Broker, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.types import OrderId, SignalAction, Symbol, TradingMode
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.mode_policy import mode_policy_violation
from runtime.models import TradeIntent
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal


def _signal() -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )


def _runtime(
    *,
    settings: Settings | None = None,
    executor: OrderExecutor | None = None,
) -> tuple[BasicTradingRuntime, MagicMock, Portfolio]:
    settings = settings or Settings(trading_mode="paper")
    portfolio = Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = _signal()
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=executor,
    )
    return runtime, market_data, portfolio


class _NonPaperBroker(Broker):
    """Minimal Broker stub that is not PaperBroker (simulates live risk)."""

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_status(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_quote(self, symbol: Symbol) -> Decimal:
        return Decimal("100")

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        return ExecutionResult(
            order_id=OrderId("live-blocked"),
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            fill_price=Decimal("100"),
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message="should never run",
        )


def test_mode_policy_allows_paper_with_dry_run_and_none() -> None:
    assert (
        mode_policy_violation(
            settings_trading_mode="paper",
            context_mode=TradingMode.PAPER,
            executor=None,
        )
        is None
    )
    assert (
        mode_policy_violation(
            settings_trading_mode="paper",
            context_mode=TradingMode.PAPER,
            executor=DryRunExecutor(),
        )
        is None
    )


def test_mode_policy_allows_paper_broker_executor() -> None:
    paper = PaperBroker(buying_power=Decimal("100000"))
    assert (
        mode_policy_violation(
            settings_trading_mode="paper",
            context_mode=TradingMode.PAPER,
            executor=BrokerOrderExecutor(paper),
        )
        is None
    )


def test_mode_policy_rejects_settings_live() -> None:
    reason = mode_policy_violation(
        settings_trading_mode="live",
        context_mode=TradingMode.PAPER,
        executor=None,
    )
    assert reason is not None
    assert "live" in reason
    assert "not allowed" in reason


def test_mode_policy_rejects_settings_backtest_and_unknown() -> None:
    backtest = mode_policy_violation(
        settings_trading_mode="backtest",
        context_mode=TradingMode.PAPER,
        executor=None,
    )
    assert backtest is not None
    assert "backtest" in backtest

    unknown = mode_policy_violation(
        settings_trading_mode="shadow",
        context_mode=TradingMode.PAPER,
        executor=None,
    )
    assert unknown is not None
    assert "shadow" in unknown


def test_mode_policy_rejects_context_live_and_backtest() -> None:
    live = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode=TradingMode.LIVE,
        executor=None,
    )
    assert live is not None
    assert "live" in live

    backtest = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode=TradingMode.BACKTEST,
        executor=None,
    )
    assert backtest is not None
    assert "backtest" in backtest


def test_mode_policy_rejects_invalid_context_mode_type() -> None:
    reason = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode="paper",
        executor=None,
    )
    assert reason is not None
    assert "invalid RuntimeContext.mode" in reason


def test_mode_policy_rejects_non_paper_broker_executor() -> None:
    reason = mode_policy_violation(
        settings_trading_mode="paper",
        context_mode=TradingMode.PAPER,
        executor=BrokerOrderExecutor(_NonPaperBroker()),
    )
    assert reason is not None
    assert "PaperBroker" in reason
    assert "blocked" in reason


def test_run_once_rejects_settings_live_before_market_data() -> None:
    runtime, market_data, portfolio = _runtime(
        settings=Settings(trading_mode="live"),
        executor=DryRunExecutor(),
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.aborted_reason is not None
    assert "live" in result.aborted_reason
    assert result.execution is None
    market_data.get_bars.assert_not_called()
    assert portfolio.summary() == before


def test_run_once_rejects_context_live() -> None:
    runtime, market_data, _portfolio = _runtime(executor=DryRunExecutor())

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.LIVE, daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.aborted_reason is not None
    assert "live" in result.aborted_reason
    market_data.get_bars.assert_not_called()


def test_run_once_rejects_context_backtest() -> None:
    runtime, market_data, _portfolio = _runtime(executor=None)

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", mode=TradingMode.BACKTEST, daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.aborted_reason is not None
    assert "backtest" in result.aborted_reason
    market_data.get_bars.assert_not_called()


def test_run_once_allows_paper_intent_only() -> None:
    runtime, market_data, portfolio = _runtime(executor=None)
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is None
    market_data.get_bars.assert_called_once()
    assert portfolio.summary() == before


def test_run_once_allows_paper_dry_run() -> None:
    runtime, _market_data, portfolio = _runtime(executor=DryRunExecutor())
    cash_before = portfolio.cash

    result = runtime.run_once(RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert portfolio.cash < cash_before


def test_run_once_allows_paper_broker_executor() -> None:
    paper = PaperBroker(buying_power=Decimal("100000"))
    paper.connect()
    runtime, _market_data, portfolio = _runtime(
        executor=BrokerOrderExecutor(paper),
    )
    cash_before = portfolio.cash

    result = runtime.run_once(RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    assert portfolio.cash < cash_before


def test_run_once_rejects_non_paper_broker_before_execute() -> None:
    broker = _NonPaperBroker()
    broker.place_order = MagicMock(wraps=broker.place_order)  # type: ignore[method-assign]
    runtime, market_data, portfolio = _runtime(
        executor=BrokerOrderExecutor(broker),
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL", mode=TradingMode.PAPER, daily_pnl_pct=Decimal("0")))

    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.aborted_reason is not None
    assert "live brokers are blocked" in result.aborted_reason
    market_data.get_bars.assert_not_called()
    broker.place_order.assert_not_called()
    assert portfolio.summary() == before


def test_run_once_still_allows_custom_non_broker_executor_stub() -> None:
    """M7 booking stubs must remain usable under paper mode."""

    class StubFilledExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            assert intent is not None
            return ExecutionResult(
                order_id=OrderId("stub"),
                symbol=intent.symbol,
                side=intent.side,
                requested_quantity=intent.quantity,
                filled_quantity=intent.quantity,
                fill_price=Decimal("100"),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message="stub fill",
            )

    runtime, _market_data, portfolio = _runtime(executor=StubFilledExecutor())
    cash_before = portfolio.cash

    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert portfolio.cash < cash_before
