"""Dry-run order execution (Milestone 5).

Simulates acceptance of a TradeIntent without contacting a broker, applying
fills, or mutating portfolio state.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.types import OrderId, OrderType, Side, Symbol
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent

# Fixed timestamp so repeated dry-runs of the same intent are deterministic.
DRY_RUN_EXECUTED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_PRICE = Decimal("0.0001")


class DryRunExecutor(OrderExecutor):
    """Safe local executor that never sends orders or touches the portfolio."""

    def execute(self, intent: TradeIntent | None) -> ExecutionResult:
        if intent is None:
            return self._reject(
                symbol=Symbol(""),
                side=Side.BUY,
                quantity=Decimal("0"),
                message="TradeIntent is required for dry-run execution",
                order_key="missing-intent",
            )

        symbol_text = str(intent.symbol).strip() if intent.symbol is not None else ""
        if not symbol_text:
            return self._reject(
                symbol=Symbol(""),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=intent.quantity,
                message="Symbol must be a non-empty string",
                order_key="empty-symbol",
            )

        if intent.quantity is None or intent.quantity <= 0:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side if isinstance(intent.side, Side) else Side.BUY,
                quantity=intent.quantity if intent.quantity is not None else Decimal("0"),
                message="Quantity must be positive",
                order_key=f"bad-qty:{symbol_text}",
            )

        if intent.side not in (Side.BUY, Side.SELL):
            return self._reject(
                symbol=Symbol(symbol_text),
                side=Side.BUY,
                quantity=intent.quantity,
                message=f"Unsupported side for dry-run: {intent.side!r}",
                order_key=f"bad-side:{symbol_text}",
            )

        if intent.order_type is not OrderType.MARKET:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=intent.side,
                quantity=intent.quantity,
                message=(
                    f"Unsupported order_type for dry-run: {intent.order_type!r} "
                    "(only MARKET is supported)"
                ),
                order_key=f"bad-type:{symbol_text}",
            )

        fill_price = self._simulated_fill_price(intent)
        order_id = self._order_id(
            f"filled|{symbol_text}|{intent.side.value}|{intent.quantity}|"
            f"{intent.order_type.value}|{intent.strategy_name}"
        )
        return ExecutionResult(
            order_id=order_id,
            symbol=Symbol(symbol_text),
            side=intent.side,
            requested_quantity=intent.quantity,
            filled_quantity=intent.quantity,
            fill_price=fill_price,
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message=(
                "Dry-run accepted: simulated fill "
                "(no broker contact, no portfolio change)"
            ),
            executed_at=DRY_RUN_EXECUTED_AT,
        )

    @staticmethod
    def _simulated_fill_price(intent: TradeIntent) -> Decimal:
        if intent.limit_price is not None and intent.limit_price > 0:
            return intent.limit_price.quantize(_PRICE)
        if intent.quantity > 0 and intent.max_position_value > 0:
            return (intent.max_position_value / intent.quantity).quantize(_PRICE)
        return Decimal("0")

    @staticmethod
    def _order_id(material: str) -> OrderId:
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return OrderId(f"dry-run-{digest}")

    def _reject(
        self,
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
        message: str,
        order_key: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            order_id=self._order_id(f"rejected|{order_key}|{message}"),
            symbol=symbol,
            side=side,
            requested_quantity=quantity if quantity is not None else Decimal("0"),
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
            executed_at=DRY_RUN_EXECUTED_AT,
        )
