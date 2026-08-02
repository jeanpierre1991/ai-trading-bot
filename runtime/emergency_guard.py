"""Broker decorator that enforces emergency halt before place_order (M14.1)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from broker_interface.broker import Broker, BrokerStatus
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.types import OrderId, Side, Symbol
from runtime.emergency_stop import EmergencyStopController


class EmergencyHaltGuardBroker(Broker):
    """Rejects place_order while halted; polls triggers and activates stop."""

    def __init__(
        self,
        inner: Broker,
        *,
        controller: EmergencyStopController,
    ) -> None:
        self._inner = inner
        self._controller = controller

    @property
    def inner(self) -> Broker:
        return self._inner

    @property
    def controller(self) -> EmergencyStopController:
        return self._controller

    def connect(self) -> bool:
        return self._inner.connect()

    def disconnect(self) -> None:
        self._inner.disconnect()

    def get_status(self) -> BrokerStatus:
        return self._inner.get_status()

    def get_quote(self, symbol: Symbol) -> Decimal:
        return self._inner.get_quote(symbol)

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        # Poll extensible triggers (file/env today).
        self._controller.poll_and_activate_if_needed()
        if self._controller.is_halted():
            state = self._controller.latch.state
            return self._reject(
                request,
                message=(
                    "EMERGENCY_HALT engaged; new exposure blocked "
                    f"(incident_id={state.incident_id})"
                ),
            )
        return self._inner.place_order(request)

    @staticmethod
    def _reject(request: BrokerOrderRequest, *, message: str) -> ExecutionResult:
        side = request.side if isinstance(request.side, Side) else Side.BUY
        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=request.symbol,
            side=side,
            requested_quantity=request.quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )
