"""Broker-backed order executor (Milestone 6).

Adapts runtime TradeIntent into a neutral BrokerOrderRequest and delegates
execution to an injected Broker. Does not mutate portfolio state.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from broker_interface.broker import Broker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.types import OrderId, OrderType, Side, Symbol
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent


class BrokerOrderExecutor(OrderExecutor):
    """OrderExecutor that routes validated intents through a Broker adapter."""

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    @property
    def broker(self) -> Broker:
        return self._broker

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        if intent is None:
            return self._reject(
                symbol=Symbol(""),
                side=Side.BUY,
                quantity=Decimal("0"),
                message="TradeIntent is required for broker execution",
            )

        symbol_text = str(intent.symbol).strip() if intent.symbol is not None else ""
        if not symbol_text:
            return self._reject(
                symbol=Symbol(""),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=intent.quantity if intent.quantity is not None else Decimal("0"),
                message="Symbol must be a non-empty string",
            )

        quantity = intent.quantity if intent.quantity is not None else Decimal("0")
        if quantity <= 0:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=quantity,
                message="Quantity must be positive",
            )

        if intent.side not in (Side.BUY, Side.SELL):
            return self._reject(
                symbol=Symbol(symbol_text),
                side=Side.BUY,
                quantity=quantity,
                message=f"Unsupported side for broker execution: {intent.side!r}",
            )

        if intent.order_type is not OrderType.MARKET:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    f"Unsupported order_type for broker execution: "
                    f"{intent.order_type!r} (only MARKET is supported)"
                ),
            )

        request = BrokerOrderRequest(
            symbol=Symbol(symbol_text),
            side=intent.side,
            order_type=intent.order_type,
            quantity=quantity,
            limit_price=intent.limit_price,
        )
        return self._broker.place_order(request)

    @staticmethod
    def _reject(
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )
