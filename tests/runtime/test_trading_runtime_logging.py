"""M8.3 runtime cycle logging (observability only)."""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from core.types import OrderId, SignalAction, TradingMode
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent
from runtime.trading_runtime import BasicTradingRuntime
from strategy_engine.signal import StrategySignal

_RUNTIME_LOGGER = "trading_bot.runtime"


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


def _runtime(
    *,
    signal: StrategySignal,
    portfolio: Portfolio | None = None,
    settings: Settings | None = None,
    executor: OrderExecutor | None = None,
) -> BasicTradingRuntime:
    settings = settings or Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
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
    )


def _messages(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == _RUNTIME_LOGGER
    )


def test_successful_dry_run_booking_emits_start_booking_and_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(signal=_signal(), executor=DryRunExecutor())

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(
            RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
        )

    text = _messages(caplog)
    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert "cycle_start" in text
    assert "symbol=AAPL" in text
    assert "execution_result" in text
    assert "status=filled" in text
    assert "booking_success" in text
    assert "stage=portfolio" in text
    assert "cycle_end" in text
    assert "success=True" in text


def test_mode_abort_logs_warning_and_preserves_m81_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(signal=_signal(), executor=DryRunExecutor())
    market_data = runtime.market_data

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(
            RuntimeContext(
                symbol="AAPL",
                mode=TradingMode.LIVE,
                daily_pnl_pct=Decimal("0"),
            ),
        )

    text = _messages(caplog)
    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.execution is None
    market_data.get_bars.assert_not_called()
    assert "mode_abort" in text
    assert "stage=mode" in text
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert "cycle_end" in text
    assert "success=False" in text


def test_risk_abort_logs_warning_without_executor_or_portfolio_mutation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        market_data_freshness_enabled=False,
    )
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    executor = MagicMock()
    runtime = _runtime(
        signal=_signal(),
        portfolio=portfolio,
        settings=settings,
        executor=executor,
    )

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    text = _messages(caplog)
    assert result.success is False
    assert result.stage_reached == "risk"
    assert "daily_pnl_pct is required" in (result.aborted_reason or "")
    executor.execute.assert_not_called()
    assert portfolio.summary() == before
    assert "risk_abort" in text
    assert "stage=risk" in text
    assert "cycle_end" in text
    assert "success=False" in text


def test_hold_success_logs_without_execution(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
        executor=DryRunExecutor(),
    )

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(RuntimeContext(symbol="AAPL"))

    text = _messages(caplog)
    assert result.success is True
    assert result.stage_reached == "risk"
    assert result.execution is None
    assert "hold_success" in text
    assert "action=hold" in text
    assert "cycle_end" in text
    assert "success=True" in text
    assert "booking_success" not in text


def test_intent_only_success_logs_without_execution(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(signal=_signal(), executor=None)

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(
            RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
        )

    text = _messages(caplog)
    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.intent is not None
    assert result.execution is None
    assert "intent_only_success" in text
    assert "cycle_end" in text
    assert "booking_success" not in text


def test_execution_rejected_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from broker_interface.broker import PaperBroker

    paper = PaperBroker(buying_power=Decimal("100000"))
    # Disconnected paper rejects orders.
    runtime = _runtime(
        signal=_signal(),
        executor=BrokerOrderExecutor(paper),
    )

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(
            RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
        )

    text = _messages(caplog)
    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.execution is not None
    assert result.execution.status.value == "rejected"
    assert "execution_result" in text
    assert "status=rejected" in text
    assert "execution_rejected" in text
    assert "cycle_end" in text
    assert "success=False" in text


def test_booking_mapper_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from broker_interface.execution import ExecutionResult, ExecutionStatus

    class InvalidFilledExecutor(OrderExecutor):
        def execute(self, intent: TradeIntent | None) -> ExecutionResult:
            assert intent is not None
            return ExecutionResult(
                order_id=OrderId("invalid"),
                symbol=intent.symbol,
                side=intent.side,
                requested_quantity=intent.quantity,
                filled_quantity=Decimal("0"),
                fill_price=Decimal("100"),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message="invalid",
            )

    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    runtime = _runtime(signal=_signal(), portfolio=portfolio, executor=InvalidFilledExecutor())

    with caplog.at_level(logging.INFO, logger=_RUNTIME_LOGGER):
        result = runtime.run_once(
            RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
        )

    text = _messages(caplog)
    assert result.success is False
    assert result.stage_reached == "execution"
    assert "execution_to_fill failed" in (result.aborted_reason or "")
    assert portfolio.summary() == before
    assert "booking_failure" in text
    assert "stage=execution" in text
    assert "cycle_end" in text
