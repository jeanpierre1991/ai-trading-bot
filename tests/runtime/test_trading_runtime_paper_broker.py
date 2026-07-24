"""End-to-end paper trading wiring: Runtime → BrokerOrderExecutor → PaperBroker."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from config.settings import Settings
from core.types import SignalAction, Symbol
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
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


class _RecordingExecutor(OrderExecutor):
    """Forwards to an inner executor while retaining the last ExecutionResult."""

    def __init__(self, inner: OrderExecutor) -> None:
        self._inner = inner
        self.last_execution: ExecutionResult | None = None

    def execute(self, intent):  # type: ignore[no-untyped-def]
        self.last_execution = self._inner.execute(intent)
        return self.last_execution


def _build_runtime(
    *,
    signal: StrategySignal,
    portfolio: Portfolio | None = None,
    broker: PaperBroker | None = None,
    connect: bool = True,
    max_position_size_pct: Decimal = Decimal("0.05"),
) -> tuple[BasicTradingRuntime, PaperBroker, _RecordingExecutor, Portfolio]:
    settings = Settings(max_position_size_pct=max_position_size_pct)
    portfolio = portfolio or Portfolio(cash=Decimal("100000"))
    paper = broker or PaperBroker(buying_power=Decimal("100000"))
    if connect:
        paper.connect()
    paper.place_order = MagicMock(wraps=paper.place_order)  # type: ignore[method-assign]
    recording = _RecordingExecutor(BrokerOrderExecutor(paper))
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
        executor=recording,
    )
    return runtime, paper, recording, portfolio


def test_buy_market_approved_reaches_paper_broker_and_fills() -> None:
    runtime, paper, recording, portfolio = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
    )
    cash_before = portfolio.cash
    quote = paper.get_quote(Symbol("AAPL"))

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert result.intent.side.value == "buy"
    assert result.execution is not None
    assert result.execution is recording.last_execution
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.execution.fill_price == quote
    assert result.execution.message == "Paper order filled"
    paper.place_order.assert_called_once()
    assert portfolio.cash < cash_before
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions


def test_risk_rejection_does_not_call_broker() -> None:
    runtime, paper, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=Portfolio(cash=Decimal("0")),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "risk"
    assert result.execution is None
    assert result.intent is None
    paper.place_order.assert_not_called()


def test_hold_does_not_call_broker() -> None:
    runtime, paper, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.intent is None
    assert result.execution is None
    paper.place_order.assert_not_called()


def test_disconnected_broker_returns_controlled_rejected() -> None:
    runtime, paper, recording, portfolio = _build_runtime(
        signal=_signal(action=SignalAction.BUY),
        connect=False,
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.intent is not None
    assert result.execution is recording.last_execution
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert result.execution.message == "Broker is disconnected"
    assert result.aborted_reason == "Broker is disconnected"
    paper.place_order.assert_called_once()
    assert portfolio.summary() == before


def test_insufficient_buying_power_returns_rejected() -> None:
    # Risk sizes from portfolio (100k → qty 50 at price 100); paper BP too low to fill.
    runtime, paper, recording, portfolio = _build_runtime(
        signal=_signal(action=SignalAction.BUY, price=Decimal("100")),
        broker=PaperBroker(buying_power=Decimal("1")),
        connect=True,
    )
    before = portfolio.summary()

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.execution is recording.last_execution
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert "Insufficient buying power" in (result.execution.message)
    assert result.aborted_reason == result.execution.message
    paper.place_order.assert_called_once()
    assert portfolio.summary() == before
    assert paper.get_status().buying_power == Decimal("1")


def test_pipeline_execution_is_exact_broker_result() -> None:
    runtime, _, recording, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY),
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert recording.last_execution is not None
    assert result.execution is recording.last_execution
    assert isinstance(result.execution, ExecutionResult)


def test_buy_filled_books_fill_via_paper_broker() -> None:
    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.apply_fill = MagicMock(wraps=portfolio.apply_fill)  # type: ignore[method-assign]
    cash_before = portfolio.cash
    runtime, _, _, _ = _build_runtime(
        signal=_signal(action=SignalAction.BUY),
        portfolio=portfolio,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.FILLED
    portfolio.apply_fill.assert_called_once()
    assert portfolio.cash < cash_before
    assert portfolio.position_count == 1
    assert "AAPL" in portfolio.positions


def test_runtime_without_executor_still_works() -> None:
    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = _signal(action=SignalAction.BUY)
    runtime = BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=portfolio,
        executor=None,
    )

    result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert result.execution is None


def test_dry_run_executor_still_works_alongside_paper_path() -> None:
    settings = Settings(max_position_size_pct=Decimal("0.05"))
    portfolio = Portfolio(cash=Decimal("100000"))
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = _signal(action=SignalAction.BUY)
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
    assert result.execution.status is ExecutionStatus.FILLED
    assert "Dry-run" in result.execution.message
