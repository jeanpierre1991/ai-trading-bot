"""M14.2 CRITICAL alert wiring + notifier failure isolation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from alerts.notifier import Alert, AlertLevel, AlertNotifier, FanoutNotifier
from broker_interface.broker import PaperBroker
from broker_interface.cancel_port import CancelAllResult
from broker_interface.execution import ExecutionResult, ExecutionStatus
from config.settings import Settings
from core.types import OrderId, SignalAction
from portfolio_manager.portfolio import Portfolio
from risk_manager.basic import BasicRiskManager
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.emergency_halt import DurableEmergencyHaltLatch
from runtime.emergency_stop import EmergencyStopController
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent
from runtime.trading_runtime import BasicTradingRuntime, _is_critical_safety_rejection
from strategy_engine.signal import StrategySignal
import runtime.trading_runtime as trading_runtime_mod


class RecordingNotifier(AlertNotifier):
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        if self.raise_error:
            raise RuntimeError("notifier boom")
        self.sent.append(alert)
        return True


class CancelPaperBroker(PaperBroker):
    def __init__(self) -> None:
        super().__init__(buying_power=Decimal("100000"))
        self.cancel_calls = 0

    def cancel_all_open_orders(self) -> CancelAllResult:
        self.cancel_calls += 1
        return CancelAllResult(attempted=True, cancelled_count=0, partial=False)


class RejectingExecutor(OrderExecutor):
    def __init__(self, message: str) -> None:
        self.message = message

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        assert intent is not None
        return ExecutionResult(
            order_id=OrderId("rej-1"),
            symbol=intent.symbol,
            side=intent.side,
            requested_quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=self.message,
        )


def _signal() -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=Decimal("100"),
    )


def _base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "trading_mode": "paper",
        "alerts_enabled": True,
        "market_data_freshness_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _runtime(
    *,
    executor: OrderExecutor,
    alert_notifier: AlertNotifier | None = None,
    execution: str = "paper",
    emergency_controller: EmergencyStopController | None = None,
    settings: Settings | None = None,
) -> BasicTradingRuntime:
    settings = settings or _base_settings()
    market_data = MagicMock()
    market_data.get_bars.return_value = [object()]
    strategy_engine = MagicMock()
    strategy_engine.evaluate.return_value = _signal()
    return BasicTradingRuntime(
        settings=settings,
        market_data=market_data,
        strategy_engine=strategy_engine,
        risk_manager=BasicRiskManager(settings),
        portfolio=Portfolio(cash=Decimal("100000")),
        executor=executor,
        alert_notifier=alert_notifier,
        execution=execution,
        emergency_controller=emergency_controller,
    )


def test_critical_safety_rejection_classifier() -> None:
    assert _is_critical_safety_rejection("TRIAL_MAX_ORDER_NOTIONAL exceeded")
    assert _is_critical_safety_rejection("EMERGENCY_HALT engaged; blocked")
    assert _is_critical_safety_rejection("LIVE_MAX_ORDERS_PER_DAY exceeded")
    assert not _is_critical_safety_rejection("insufficient buying power")


def test_trial_limit_rejection_emits_critical_alert() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        executor=RejectingExecutor(
            "TRIAL_MAX_ORDERS_PER_MINUTE exceeded; order blocked before broker submission"
        ),
        alert_notifier=notifier,
    )
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert result.alerts_sent == 1
    assert notifier.sent[0].level is AlertLevel.CRITICAL
    assert notifier.sent[0].title == "execution_rejected"


def test_ordinary_rejection_remains_warning() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        executor=RejectingExecutor("insufficient buying power"),
        alert_notifier=notifier,
    )
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert notifier.sent[0].level is AlertLevel.WARNING


def test_emergency_halt_cycle_abort_emits_critical(tmp_path: Path) -> None:
    latch = DurableEmergencyHaltLatch(tmp_path / "halt.json")
    latch.engage(incident_id="inc-1", reason="test", trigger_source="file")
    controller = EmergencyStopController(
        latch=latch,
        broker=CancelPaperBroker(),
        incident_dir=tmp_path / "incidents",
        notifier=RecordingNotifier(),
    )
    notifier = RecordingNotifier()
    runtime = _runtime(
        executor=BrokerOrderExecutor(PaperBroker(buying_power=Decimal("100000"))),
        alert_notifier=notifier,
        execution="live",
        emergency_controller=controller,
    )
    original = trading_runtime_mod.mode_policy_violation
    trading_runtime_mod.mode_policy_violation = lambda **kwargs: None  # type: ignore[assignment]
    try:
        result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    finally:
        trading_runtime_mod.mode_policy_violation = original  # type: ignore[assignment]

    assert result.success is False
    assert result.stage_reached == "emergency_halt"
    assert result.alerts_sent == 1
    assert notifier.sent[0].level is AlertLevel.CRITICAL
    assert notifier.sent[0].title == "emergency_halt_cycle_abort"


def test_reconcile_abort_emits_critical() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        executor=BrokerOrderExecutor(PaperBroker(buying_power=Decimal("100000"))),
        alert_notifier=notifier,
        execution="live",
    )
    original_mode = trading_runtime_mod.mode_policy_violation
    trading_runtime_mod.mode_policy_violation = lambda **kwargs: None  # type: ignore[assignment]
    runtime._live_reconcile_abort_reason = (  # type: ignore[method-assign]
        lambda: "open/unknown live ledger entries require abort-only reconcile"
    )
    try:
        result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    finally:
        trading_runtime_mod.mode_policy_violation = original_mode  # type: ignore[assignment]

    assert result.success is False
    assert result.stage_reached == "reconcile"
    assert result.alerts_sent == 1
    assert notifier.sent[0].level is AlertLevel.CRITICAL
    assert notifier.sent[0].title == "reconcile_abort"


def test_live_mode_gate_denial_emits_critical() -> None:
    notifier = RecordingNotifier()
    runtime = _runtime(
        executor=BrokerOrderExecutor(PaperBroker(buying_power=Decimal("100000"))),
        alert_notifier=notifier,
        execution="live",
        settings=_base_settings(trading_mode="paper"),
    )
    result = runtime.run_once(RuntimeContext(symbol="AAPL", daily_pnl_pct=Decimal("0")))
    assert result.success is False
    assert result.stage_reached == "mode"
    assert result.alerts_sent == 1
    assert notifier.sent[0].level is AlertLevel.CRITICAL
    assert notifier.sent[0].title == "live_mode_gate_denied"


def test_emergency_stop_completes_when_notifier_raises(tmp_path: Path) -> None:
    broker = CancelPaperBroker()
    latch = DurableEmergencyHaltLatch(tmp_path / "halt.json")
    controller = EmergencyStopController(
        latch=latch,
        broker=broker,
        incident_dir=tmp_path / "incidents",
        notifier=RecordingNotifier(raise_error=True),
        triggers=[],
    )
    result = controller.activate(reason="manual_test", trigger_source="cli")
    assert result.activated is True
    assert result.alert_sent is False
    assert latch.is_engaged() is True
    assert broker.cancel_calls == 1
    assert result.incident_id


def test_fanout_failure_does_not_undo_emergency_stop(tmp_path: Path) -> None:
    class AlwaysFail(AlertNotifier):
        def send(self, alert: Alert) -> bool:
            return False

    broker = CancelPaperBroker()
    latch = DurableEmergencyHaltLatch(tmp_path / "halt.json")
    controller = EmergencyStopController(
        latch=latch,
        broker=broker,
        incident_dir=tmp_path / "incidents",
        notifier=FanoutNotifier([AlwaysFail(), RecordingNotifier(raise_error=True)]),
        triggers=[],
    )
    result = controller.activate(reason="kill", trigger_source="file")
    assert result.activated is True
    assert latch.is_engaged() is True
    assert broker.cancel_calls == 1
