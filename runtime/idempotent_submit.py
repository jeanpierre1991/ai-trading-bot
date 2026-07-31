"""Idempotent live sandbox submit path (Milestone 13.3).

Generates stable UUIDv4 ``client_order_id`` per new logical order (I1), reuses
the existing id for supervised FAILED_ABSENT retries, enforces one open order
per symbol (I2), and maps ambiguous transport failures to UNKNOWN without
minting a new id.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from broker_interface.broker import Broker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, Symbol
from runtime.broker_executor import BrokerOrderExecutor
from runtime.live_order_ledger import JsonLiveOrderLedger, LiveOrderState
from runtime.models import TradeIntent


class IdempotentLiveExecutor(BrokerOrderExecutor):
    """BrokerOrderExecutor that persists ledger identity before venue I/O."""

    def __init__(self, broker: Broker, *, ledger: JsonLiveOrderLedger) -> None:
        super().__init__(broker)
        self._ledger = ledger

    @property
    def ledger(self) -> JsonLiveOrderLedger:
        return self._ledger

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        if intent is None:
            return self._reject_msg(
                symbol=Symbol(""),
                side=Side.BUY,
                quantity=Decimal("0"),
                message="TradeIntent is required for broker execution",
            )

        symbol_text = str(intent.symbol).strip() if intent.symbol is not None else ""
        if not symbol_text:
            return self._reject_msg(
                symbol=Symbol(""),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=intent.quantity if intent.quantity is not None else Decimal("0"),
                message="Symbol must be a non-empty string",
            )

        quantity = intent.quantity if intent.quantity is not None else Decimal("0")
        if quantity <= 0:
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=quantity,
                message="Quantity must be positive",
            )

        if intent.side not in (Side.BUY, Side.SELL):
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=Side.BUY,
                quantity=quantity,
                message=f"Unsupported side for broker execution: {intent.side!r}",
            )

        if intent.order_type is not OrderType.MARKET:
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    f"Unsupported order_type for broker execution: "
                    f"{intent.order_type!r} (only MARKET is supported)"
                ),
            )

        if self._ledger.has_blocking_unknown():
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    "live ledger has UNKNOWN order(s); "
                    "refusing new submit until reconcile clears (fail closed)"
                ),
            )

        existing_open = self._ledger.open_order_for_symbol(symbol_text)
        if existing_open is not None:
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    f"I2: open live order already exists for {symbol_text} "
                    f"(client_order_id={existing_open.client_order_id})"
                ),
            )

        retryable = self._ledger.retryable_order_for_symbol(symbol_text)
        if retryable is not None:
            record = self._ledger.prepare_retry(
                retryable.client_order_id,
                side=intent.side,
                order_type=intent.order_type,
                quantity=quantity,
            )
        else:
            record = self._ledger.create_order(
                symbol=Symbol(symbol_text),
                side=intent.side,
                order_type=intent.order_type,
                quantity=quantity,
                client_order_id=str(uuid.uuid4()),
            )
            self._ledger.update_state(record.client_order_id, LiveOrderState.SUBMITTING)

        request = BrokerOrderRequest(
            symbol=Symbol(symbol_text),
            side=intent.side,
            order_type=intent.order_type,
            quantity=quantity,
            limit_price=intent.limit_price,
            client_order_id=record.client_order_id,
        )

        try:
            result = self._broker.place_order(request)
        except Exception as exc:
            self._ledger.update_state(
                record.client_order_id,
                LiveOrderState.UNKNOWN,
                last_error=exc.__class__.__name__,
            )
            return self._reject_msg(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=quantity,
                message=(
                    "ambiguous live submit failure marked UNKNOWN; "
                    f"{exc.__class__.__name__}"
                ),
            )

        return self._interpret_result(record.client_order_id, result)

    def _interpret_result(
        self,
        client_order_id: str,
        result: ExecutionResult,
    ) -> ExecutionResult:
        message = (result.message or "").lower()
        broker_oid = str(result.order_id) if result.order_id is not None else None

        if result.status is ExecutionStatus.FILLED:
            self._ledger.update_state(
                client_order_id,
                LiveOrderState.FILLED,
                broker_order_id=broker_oid,
                filled_quantity=result.filled_quantity,
                avg_fill_price=result.fill_price,
            )
            return result

        # Ambiguous transport / timeout: fail closed as UNKNOWN (no new id).
        if any(
            token in message
            for token in (
                "timeout",
                "network failure",
                "temporarily",
                "connection",
            )
        ):
            self._ledger.update_state(
                client_order_id,
                LiveOrderState.UNKNOWN,
                broker_order_id=broker_oid,
                last_error=result.message,
            )
            return ExecutionResult(
                order_id=result.order_id,
                symbol=result.symbol,
                side=result.side,
                requested_quantity=result.requested_quantity,
                filled_quantity=Decimal("0"),
                fill_price=Decimal("0"),
                fee=Decimal("0"),
                status=ExecutionStatus.REJECTED,
                message=(
                    f"UNKNOWN live submit outcome for client_order_id={client_order_id}; "
                    f"{result.message}"
                ),
            )

        # Open/accepted mapped by adapter as non-fill reject — track as open.
        if "not filled" in message or "status=accepted" in message or "status=new" in message:
            self._ledger.update_state(
                client_order_id,
                LiveOrderState.ACCEPTED_OPEN,
                broker_order_id=broker_oid,
                last_error=result.message,
            )
            return result

        self._ledger.update_state(
            client_order_id,
            LiveOrderState.REJECTED,
            broker_order_id=broker_oid,
            last_error=result.message,
        )
        return result

    @staticmethod
    def _reject_msg(
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


def require_live_ledger_path(path: object) -> str:
    text = str(path or "").strip()
    if not text:
        raise ConfigurationError(
            "execution='live' requires LIVE_ORDER_LEDGER_PATH "
            "(atomic JSON live order ledger)"
        )
    return text
