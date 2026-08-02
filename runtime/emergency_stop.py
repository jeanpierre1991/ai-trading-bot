"""Emergency stop controller (Milestone 14.1).

Extensible trigger architecture: current implementation polls file/env sources.
Future CLI/webhook triggers can implement ``EmergencyStopTrigger`` without
changing activation semantics.

Activation:
  1) mint unique Incident ID
  2) durable halt latch engage
  3) best-effort cancel_all_open_orders
  4) write incident snapshot
  5) CRITICAL alert (console acceptable in M14.1)
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from alerts.notifier import Alert, AlertLevel, AlertNotifier
from broker_interface.cancel_port import CancelAllResult, CancelCapableBroker
from broker_interface.reconcile_port import ReconcileCapableBroker
from core.exceptions import ConfigurationError
from runtime.emergency_halt import DurableEmergencyHaltLatch
from runtime.kill_switch import FileEnvKillSwitch


class TriggerSource(str, Enum):
    """Known emergency-stop trigger sources (extensible)."""

    FILE = "file"
    ENV = "env"
    CLI = "cli"  # reserved for future
    WEBHOOK = "webhook"  # reserved for future
    MANUAL = "manual"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TriggerEvent:
    source: TriggerSource
    reason: str


class EmergencyStopTrigger(Protocol):
    """Pollable trigger source for emergency stop (file/env now; CLI/webhook later)."""

    def poll(self) -> TriggerEvent | None:
        """Return a TriggerEvent when this source requests emergency stop."""


class FileEnvEmergencyTrigger:
    """Current mechanism: kill file presence and/or truthy env var."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        env_var: str = "LIVE_EMERGENCY_KILL",
    ) -> None:
        self._switch = FileEnvKillSwitch(path, env_var=env_var)
        self._path = Path(path) if path is not None else None
        self._env_var = env_var

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def env_var(self) -> str:
        return self._env_var

    def poll(self) -> TriggerEvent | None:
        import os as _os

        raw = _os.environ.get(self._env_var)
        if raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}:
            return TriggerEvent(
                source=TriggerSource.ENV,
                reason=f"emergency kill env engaged ({self._env_var})",
            )
        if self._path is not None and self._path.exists():
            return TriggerEvent(
                source=TriggerSource.FILE,
                reason=f"emergency kill file present ({self._path})",
            )
        return None


@dataclass(frozen=True)
class EmergencyStopResult:
    activated: bool
    incident_id: str | None
    already_halted: bool
    cancel_result: CancelAllResult | None
    snapshot_path: str | None
    alert_sent: bool
    reason: str
    trigger_source: str


class EmergencyStopController:
    """Coordinates halt latch, cancel-all, incident snapshot, and CRITICAL alert."""

    def __init__(
        self,
        *,
        latch: DurableEmergencyHaltLatch,
        incident_dir: Path | str,
        broker: Any | None = None,
        notifier: AlertNotifier | None = None,
        triggers: list[EmergencyStopTrigger] | None = None,
    ) -> None:
        self._latch = latch
        self._incident_dir = Path(incident_dir)
        self._broker = broker
        self._notifier = notifier
        self._triggers = list(triggers or [])
        try:
            self._incident_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"failed to create incident directory {self._incident_dir}: {exc}"
            ) from exc

    @property
    def latch(self) -> DurableEmergencyHaltLatch:
        return self._latch

    def is_halted(self) -> bool:
        return self._latch.is_engaged()

    def poll_and_activate_if_needed(self) -> EmergencyStopResult | None:
        """Poll configured triggers; activate once if any is engaged."""
        if self._latch.is_engaged():
            return None
        for trigger in self._triggers:
            event = trigger.poll()
            if event is not None:
                return self.activate(
                    reason=event.reason,
                    trigger_source=event.source,
                )
        return None

    def activate(
        self,
        *,
        reason: str,
        trigger_source: TriggerSource | str = TriggerSource.MANUAL,
    ) -> EmergencyStopResult:
        source = (
            trigger_source.value
            if isinstance(trigger_source, TriggerSource)
            else str(trigger_source or TriggerSource.UNKNOWN.value)
        )
        reason_text = str(reason or "emergency_stop").strip() or "emergency_stop"

        if self._latch.is_engaged():
            state = self._latch.state
            return EmergencyStopResult(
                activated=False,
                incident_id=state.incident_id,
                already_halted=True,
                cancel_result=None,
                snapshot_path=None,
                alert_sent=False,
                reason=state.reason or reason_text,
                trigger_source=state.trigger_source or source,
            )

        incident_id = str(uuid.uuid4())
        self._latch.engage(
            incident_id=incident_id,
            reason=reason_text,
            trigger_source=source,
        )

        cancel_result = self._best_effort_cancel()
        snapshot_path = self._write_incident_snapshot(
            incident_id=incident_id,
            reason=reason_text,
            trigger_source=source,
            cancel_result=cancel_result,
        )
        alert_sent = self._emit_critical(
            incident_id=incident_id,
            reason=reason_text,
            trigger_source=source,
            cancel_result=cancel_result,
            snapshot_path=snapshot_path,
        )
        return EmergencyStopResult(
            activated=True,
            incident_id=incident_id,
            already_halted=False,
            cancel_result=cancel_result,
            snapshot_path=str(snapshot_path) if snapshot_path is not None else None,
            alert_sent=alert_sent,
            reason=reason_text,
            trigger_source=source,
        )

    def _best_effort_cancel(self) -> CancelAllResult:
        capable = _as_cancel_broker(self._broker)
        if capable is None:
            return CancelAllResult(
                attempted=False,
                partial=True,
                error="broker_not_cancel_capable",
                message=(
                    "emergency stop: broker does not implement CancelCapableBroker; "
                    "local halt engaged without cancel-all"
                ),
            )
        try:
            result = capable.cancel_all_open_orders()
        except Exception as exc:  # noqa: BLE001 - best-effort
            return CancelAllResult(
                attempted=True,
                partial=True,
                error=exc.__class__.__name__,
                message=f"cancel_all_open_orders raised {exc.__class__.__name__}",
            )
        if not isinstance(result, CancelAllResult):
            return CancelAllResult(
                attempted=True,
                partial=True,
                error="invalid_cancel_result",
                message="cancel_all_open_orders returned invalid result type",
            )
        return result

    def _write_incident_snapshot(
        self,
        *,
        incident_id: str,
        reason: str,
        trigger_source: str,
        cancel_result: CancelAllResult,
    ) -> Path | None:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        positions: list[dict[str, Any]] = []
        open_orders: list[dict[str, Any]] = []
        account: dict[str, Any] | None = None

        venue = _peel_decorators(self._broker)
        if isinstance(venue, ReconcileCapableBroker):
            try:
                for pos in venue.list_positions():
                    positions.append(
                        {
                            "symbol": str(pos.symbol),
                            "quantity": str(pos.quantity),
                            "avg_entry_price": (
                                str(pos.avg_entry_price)
                                if pos.avg_entry_price is not None
                                else None
                            ),
                        }
                    )
            except Exception:  # noqa: BLE001
                positions = [{"error": "positions_unavailable"}]
            try:
                for order in venue.list_open_orders():
                    open_orders.append(
                        {
                            "broker_order_id": order.broker_order_id,
                            "client_order_id": order.client_order_id,
                            "symbol": str(order.symbol),
                            "side": order.side.value,
                            "quantity": str(order.quantity),
                            "status": order.status.value,
                        }
                    )
            except Exception:  # noqa: BLE001
                open_orders = [{"error": "open_orders_unavailable"}]

        if venue is not None and hasattr(venue, "get_status"):
            try:
                status = venue.get_status()
                account = {
                    "broker_name": getattr(status, "broker_name", None),
                    "account_id": getattr(status, "account_id", None),
                    "buying_power": str(getattr(status, "buying_power", "")),
                    "connected": bool(getattr(status, "connected", False)),
                }
            except Exception:  # noqa: BLE001
                account = {"error": "account_unavailable"}

        payload = {
            "incident_id": incident_id,
            "timestamp": ts,
            "reason": reason,
            "trigger_source": trigger_source,
            "positions": positions,
            "open_orders": open_orders,
            "account": account,
            "cancel_all": {
                "attempted": cancel_result.attempted,
                "partial": cancel_result.partial,
                "canceled_order_ids": list(cancel_result.canceled_order_ids),
                "failed_order_ids": list(cancel_result.failed_order_ids),
                "error": cancel_result.error,
                "message": cancel_result.message,
            },
        }
        path = self._incident_dir / f"{incident_id}.json"
        try:
            text = json.dumps(payload, indent=2, sort_keys=True)
            path.write_text(text + "\n", encoding="utf-8")
            return path
        except OSError:
            return None

    def _emit_critical(
        self,
        *,
        incident_id: str,
        reason: str,
        trigger_source: str,
        cancel_result: CancelAllResult,
        snapshot_path: Path | None,
    ) -> bool:
        if self._notifier is None:
            return False
        message = (
            f"incident_id={incident_id} trigger={trigger_source} reason={reason} "
            f"cancel_attempted={cancel_result.attempted} "
            f"cancel_partial={cancel_result.partial} "
            f"snapshot={snapshot_path}"
        )
        alert = Alert(
            title="emergency_stop_activated",
            message=message,
            level=AlertLevel.CRITICAL,
            source="emergency_stop",
        )
        try:
            return bool(self._notifier.send(alert))
        except Exception:  # noqa: BLE001 - alert must not undo halt
            return False


def _peel_decorators(broker: Any) -> Any:
    """Peel known broker decorators without importing live_caps (avoid cycles)."""
    current = broker
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "inner", None)
        if inner is None or inner is current:
            break
        current = inner
    return current


def _as_cancel_broker(broker: Any) -> CancelCapableBroker | None:
    inner = _peel_decorators(broker)
    if isinstance(inner, CancelCapableBroker):
        return inner
    return None


def require_emergency_halt_path(path: object) -> Path:
    text = str(path or "").strip()
    if not text:
        raise ConfigurationError(
            "execution='live' requires LIVE_EMERGENCY_HALT_PATH "
            "(durable emergency halt latch)"
        )
    return Path(text)
