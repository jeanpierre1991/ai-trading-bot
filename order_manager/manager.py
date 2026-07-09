"""Order management logic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from core.types import OrderId, OrderType, Side, Symbol


class OrderState(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class OrderRecord:
    order_id: OrderId
    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    state: OrderState = OrderState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "order_id": str(self.order_id),
            "symbol": str(self.symbol),
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": float(self.quantity),
            "price": float(self.price) if self.price else None,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
        }


class OrderManager:
    """In-memory order book for paper trading and backtesting."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderRecord] = {}

    @property
    def order_count(self) -> int:
        return len(self._orders)

    def create_order(
        self,
        symbol: Symbol,
        side: Side,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None = None,
    ) -> OrderRecord:
        order_id = OrderId(str(uuid.uuid4()))
        record = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )
        self._orders[str(order_id)] = record
        return record

    def get_order(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    def list_orders(self) -> list[OrderRecord]:
        return list(self._orders.values())

    def update_state(self, order_id: str, state: OrderState) -> OrderRecord | None:
        record = self._orders.get(order_id)
        if record is None:
            return None
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        return record
