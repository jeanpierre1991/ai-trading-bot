"""Broker execution result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from core.types import OrderId, Side, Symbol


class ExecutionStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExecutionResult:
    order_id: OrderId
    symbol: Symbol
    side: Side
    requested_quantity: Decimal
    filled_quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    status: ExecutionStatus
    message: str
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
