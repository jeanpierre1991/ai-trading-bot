"""Sandbox controlled-trial protocol dry-run (Milestone 14.4).

Executes the supervised emergency-stop protocol without real money:
kill/activate → durable halt → best-effort cancel-all → CRITICAL alert →
incident snapshot. Broker-agnostic; uses injected components only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alerts.notifier import AlertNotifier
from broker_interface.cancel_port import CancelAllResult
from runtime.emergency_halt import DurableEmergencyHaltLatch
from runtime.emergency_stop import (
    EmergencyStopController,
    EmergencyStopResult,
    EmergencyStopTrigger,
    TriggerSource,
)


@dataclass(frozen=True)
class ProtocolDryRunResult:
    """Structured outcome of one sandbox protocol dry-run."""

    success: bool
    activated: bool
    already_halted: bool
    incident_id: str | None
    halt_engaged: bool
    cancel_attempted: bool
    cancel_partial: bool
    alert_sent: bool
    snapshot_path: str | None
    reason: str
    trigger_source: str
    place_order_blocked: bool
    notes: tuple[str, ...] = ()


def run_sandbox_emergency_protocol(
    *,
    latch_path: Path,
    incident_dir: Path,
    broker: Any,
    notifier: AlertNotifier | None = None,
    triggers: list[EmergencyStopTrigger] | None = None,
    reason: str = "m14_4_sandbox_protocol_dry_run",
    trigger_source: TriggerSource | str = TriggerSource.MANUAL,
    verify_place_order_blocked: bool = True,
) -> ProtocolDryRunResult:
    """Run kill → halt → cancel-all → CRITICAL alert protocol in-process.

    Never enables LIVE_PRODUCTION. Failures in alert delivery do not undo halt.
    """
    latch = DurableEmergencyHaltLatch(latch_path)
    controller = EmergencyStopController(
        latch=latch,
        incident_dir=incident_dir,
        broker=broker,
        notifier=notifier,
        triggers=list(triggers or []),
    )
    result: EmergencyStopResult = controller.activate(
        reason=reason,
        trigger_source=trigger_source,
    )

    place_blocked = False
    notes: list[str] = []
    if verify_place_order_blocked and hasattr(broker, "place_order"):
        from decimal import Decimal

        from broker_interface.execution import ExecutionStatus
        from broker_interface.orders import BrokerOrderRequest
        from core.types import OrderType, Side, Symbol
        from runtime.emergency_guard import EmergencyHaltGuardBroker

        guarded = EmergencyHaltGuardBroker(broker, controller=controller)
        rejection = guarded.place_order(
            BrokerOrderRequest(
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("1"),
            )
        )
        place_blocked = (
            rejection.status is ExecutionStatus.REJECTED
            and "EMERGENCY_HALT" in (rejection.message or "")
        )
        if not place_blocked:
            notes.append("place_order was not blocked after halt")

    cancel = result.cancel_result or CancelAllResult(attempted=False, partial=True)
    success = bool(
        (result.activated or result.already_halted)
        and controller.is_halted()
        and (place_blocked if verify_place_order_blocked else True)
    )
    if result.activated and not result.alert_sent:
        notes.append("CRITICAL alert not confirmed (halt still engaged)")
    if cancel.partial:
        notes.append("cancel-all reported partial/failure; halt remains engaged")

    return ProtocolDryRunResult(
        success=success,
        activated=result.activated,
        already_halted=result.already_halted,
        incident_id=result.incident_id,
        halt_engaged=controller.is_halted(),
        cancel_attempted=bool(cancel.attempted),
        cancel_partial=bool(cancel.partial),
        alert_sent=bool(result.alert_sent),
        snapshot_path=result.snapshot_path,
        reason=result.reason,
        trigger_source=result.trigger_source,
        place_order_blocked=place_blocked,
        notes=tuple(notes),
    )
