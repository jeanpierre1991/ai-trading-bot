"""M8.5 optional AlertNotifier wiring in BasicTradingRuntime."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from alerts.notifier import Alert, AlertLevel, AlertNotifier
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


class RecordingNotifier(AlertNotifier):
    def __init__(self, *, succeed: bool = True, raise_error: bool = False) -> None:
        self.succeed = succeed
        self.raise_error = raise_error
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        if self.raise_error:
            raise RuntimeError("notifier boom")
        self.sent.append(alert)
        return self.succeed


def _signal(
    *,
    action: SignalAction = SignalAction.BUY,
    price: Decimal = Decimal("100"),
) -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=action,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=price,
    )


def _settings(*, alerts_enabled: bool = True) -> Settings:
    return Settings(
        trading_mode="paper",
        max_position_size_pct=Decimal("0.05"),
        max_open_positions=10,
        max_daily_loss_pct=Decimal("0.02"),
        alerts_enabled=alerts_enabled,
        market_data_freshness_enabled=False,
    )


def _runtime(
    *,
    signal: StrategySignal,
    executor: OrderExecutor | None = None,
    alert_notifier: AlertNotifier | None = None,
    settings: Settings | None = None,
    portfolio: Portfolio | None = None,
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
        alert_notifier=alert_notifier,
    )


def test_without_notifier_keeps_alerts_sent_zero_on_booking_success() -> None:
    runtime = _runtime(signal=_signal(), executor=DryRunExecutor())

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.alerts_sent == 0


def test_booking_success_emits_info_alert() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        signal=_signal(),
        executor=DryRunExecutor(),
        alert_notifier=notifier,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.alerts_sent == 1
    assert len(notifier.sent) == 1
    assert notifier.sent[0].title == "booking_success"
    assert notifier.sent[0].level is AlertLevel.INFO
    assert "AAPL" in notifier.sent[0].message


def test_execution_rejected_emits_warning_alert() -> None:
    notifier = RecordingNotifier()
    paper = PaperBroker(buying_power=Decimal("100000"))
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    runtime = _runtime(
        signal=_signal(),
        portfolio=portfolio,
        executor=BrokerOrderExecutor(paper),
        alert_notifier=notifier,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "execution"
    assert result.execution is not None
    assert result.execution.status is ExecutionStatus.REJECTED
    assert result.alerts_sent == 1
    assert notifier.sent[0].title == "execution_rejected"
    assert notifier.sent[0].level is AlertLevel.WARNING
    assert portfolio.summary() == before


def test_apply_fill_failure_emits_booking_failed_alert() -> None:
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
                message="Forced filled sell",
            )

    notifier = RecordingNotifier()
    portfolio = Portfolio(cash=Decimal("100000"))
    before = portfolio.summary()
    runtime = _runtime(
        signal=_signal(),
        portfolio=portfolio,
        executor=ForcedSellFilledExecutor(),
        alert_notifier=notifier,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is False
    assert result.stage_reached == "portfolio"
    assert "apply_fill failed" in (result.aborted_reason or "")
    assert result.alerts_sent == 1
    assert notifier.sent[0].title == "booking_failed"
    assert notifier.sent[0].level is AlertLevel.ERROR
    assert portfolio.summary() == before


def test_alerts_disabled_does_not_send() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        signal=_signal(),
        executor=DryRunExecutor(),
        alert_notifier=notifier,
        settings=_settings(alerts_enabled=False),
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.alerts_sent == 0
    assert notifier.sent == []


def test_notifier_returning_false_does_not_count_or_break_cycle() -> None:
    notifier = RecordingNotifier(succeed=False)
    runtime = _runtime(
        signal=_signal(),
        executor=DryRunExecutor(),
        alert_notifier=notifier,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.alerts_sent == 0
    assert len(notifier.sent) == 1


def test_notifier_exception_does_not_break_cycle() -> None:
    notifier = RecordingNotifier(raise_error=True)
    runtime = _runtime(
        signal=_signal(),
        executor=DryRunExecutor(),
        alert_notifier=notifier,
    )

    result = runtime.run_once(
        RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")),
    )

    assert result.success is True
    assert result.stage_reached == "portfolio"
    assert result.execution is not None
    assert result.alerts_sent == 0
    assert notifier.sent == []


def test_hold_and_risk_abort_do_not_emit_alerts() -> None:
    hold_notifier = RecordingNotifier()
    hold_runtime = _runtime(
        signal=_signal(action=SignalAction.HOLD, price=Decimal("0")),
        executor=DryRunExecutor(),
        alert_notifier=hold_notifier,
    )
    hold_result = hold_runtime.run_once(RuntimeContext(symbol="AAPL"))
    assert hold_result.success is True
    assert hold_result.stage_reached == "risk"
    assert hold_result.alerts_sent == 0
    assert hold_notifier.sent == []

    risk_notifier = RecordingNotifier()
    risk_executor = MagicMock()
    risk_runtime = _runtime(
        signal=_signal(),
        executor=risk_executor,
        alert_notifier=risk_notifier,
    )
    risk_result = risk_runtime.run_once(RuntimeContext(symbol="AAPL"))
    assert risk_result.success is False
    assert risk_result.stage_reached == "risk"
    assert risk_result.alerts_sent == 0
    assert risk_notifier.sent == []
    risk_executor.execute.assert_not_called()
