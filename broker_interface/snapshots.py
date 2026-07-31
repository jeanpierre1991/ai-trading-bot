"""Broker-agnostic order/position snapshots for M13.3 reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from core.types import Side, Symbol


class BrokerOrderStatus(str, Enum):
    """Normalized venue order status for observe/diff (not local ledger states)."""

    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str | None
    symbol: Symbol
    side: Side
    quantity: Decimal
    filled_quantity: Decimal
    status: BrokerOrderStatus
    avg_fill_price: Decimal | None = None
    raw_status: str | None = None


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    symbol: Symbol
    quantity: Decimal
    avg_entry_price: Decimal | None = None
