"""Neutral order request DTOs for broker adapters.

These types intentionally do not depend on runtime.TradeIntent so broker
implementations stay reusable across paper, live, and future adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.types import OrderType, Side, Symbol


@dataclass(frozen=True)
class BrokerOrderRequest:
    """Broker-facing order payload independent of strategy/runtime metadata.

    ``client_order_id`` is an optional broker-agnostic idempotency key that
    adapters may forward when the venue supports it. Core runtime does not
    require it for paper/local brokers (M13.1 additive contract).
    """

    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    client_order_id: str | None = None
