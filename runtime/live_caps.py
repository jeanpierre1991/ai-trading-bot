"""Absolute LIVE sandbox order caps (Milestone 13.2 G10 enforcement).

In-process UTC-day counter (D-C). Durable enforcement deferred to M13.3/M14.
Fail closed before broker ``place_order``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from broker_interface.broker import Broker, BrokerStatus
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.types import OrderId, Side, Symbol


class LiveOrderCounter:
    """In-process submit counter keyed by UTC calendar day."""

    def __init__(self) -> None:
        self._utc_day: str | None = None
        self._count: int = 0

    def current_count(self, *, now: datetime | None = None) -> int:
        day = _utc_day(now)
        if self._utc_day != day:
            return 0
        return self._count

    def record_submit(self, *, now: datetime | None = None) -> None:
        day = _utc_day(now)
        if self._utc_day != day:
            self._utc_day = day
            self._count = 0
        self._count += 1


def _utc_day(now: datetime | None) -> str:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current.date().isoformat()


class LiveCapGuardBroker(Broker):
    """Decorator that enforces notional + daily order caps before place_order."""

    def __init__(
        self,
        inner: Broker,
        *,
        max_order_notional: Decimal,
        max_orders_per_day: int,
        counter: LiveOrderCounter | None = None,
    ) -> None:
        self._inner = inner
        self._max_order_notional = Decimal(str(max_order_notional))
        self._max_orders_per_day = int(max_orders_per_day)
        self._counter = counter if counter is not None else LiveOrderCounter()

    @property
    def inner(self) -> Broker:
        return self._inner

    @property
    def counter(self) -> LiveOrderCounter:
        return self._counter

    def connect(self) -> bool:
        return self._inner.connect()

    def disconnect(self) -> None:
        self._inner.disconnect()

    def get_status(self) -> BrokerStatus:
        return self._inner.get_status()

    def get_quote(self, symbol: Symbol) -> Decimal:
        return self._inner.get_quote(symbol)

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        if self._counter.current_count() >= self._max_orders_per_day:
            return self._reject(
                request,
                message=(
                    "LIVE_MAX_ORDERS_PER_DAY exceeded; "
                    "order blocked before broker submission"
                ),
            )

        try:
            quote = self._inner.get_quote(request.symbol)
        except Exception as exc:
            return self._reject(
                request,
                message=(
                    "LIVE notional cap check failed: quote unavailable "
                    f"({exc.__class__.__name__})"
                ),
            )

        notional = Decimal(str(request.quantity)) * Decimal(str(quote))
        if notional > self._max_order_notional:
            return self._reject(
                request,
                message=(
                    "LIVE_MAX_ORDER_NOTIONAL exceeded; "
                    "order blocked before broker submission"
                ),
            )

        # Count the submission attempt after cap checks, before venue I/O.
        self._counter.record_submit()
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
