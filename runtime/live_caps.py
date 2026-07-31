"""Absolute LIVE sandbox order caps (Milestone 13.2 G10 + M13.3 C1).

In-process UTC-day counter, durable via ledger ``first_submit_counted`` /
``first_submit_day``. Same ``client_order_id`` retry does not consume another
daily slot across process restart (Decision C1). Fail closed before broker
``place_order``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from broker_interface.broker import Broker, BrokerStatus
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.exceptions import ConfigurationError
from core.types import OrderId, Side, Symbol

if TYPE_CHECKING:
    from runtime.live_order_ledger import JsonLiveOrderLedger


@dataclass(frozen=True)
class CapEvaluation:
    """Read-only cap check result (does not mutate counters)."""

    order_notional_ok: bool
    daily_count_ok: bool
    notional: Decimal
    current_count: int
    would_consume_slot: bool = False


def evaluate_order_caps(
    *,
    quantity: Decimal,
    quote: Decimal,
    max_order_notional: Decimal,
    max_orders_per_day: int,
    current_count: int,
    already_counted: bool = False,
) -> CapEvaluation:
    """Hypothetically evaluate G10 caps without recording a submit."""
    notional = Decimal(str(quantity)) * Decimal(str(quote))
    order_ok = notional <= Decimal(str(max_order_notional))
    if already_counted:
        daily_ok = True
        would_consume = False
    else:
        daily_ok = int(current_count) < int(max_orders_per_day)
        would_consume = False  # shadow / dry evaluation never consumes
    return CapEvaluation(
        order_notional_ok=order_ok,
        daily_count_ok=daily_ok,
        notional=notional,
        current_count=int(current_count),
        would_consume_slot=would_consume,
    )


class LiveOrderCounter:
    """In-process submit counter keyed by UTC calendar day."""

    def __init__(self) -> None:
        self._utc_day: str | None = None
        self._count: int = 0
        self._counted_client_ids: set[str] = set()

    def current_count(self, *, now: datetime | None = None) -> int:
        self._roll_day(now)
        return self._count

    def already_counted(
        self, client_order_id: str | None, *, now: datetime | None = None
    ) -> bool:
        self._roll_day(now)
        cid = (client_order_id or "").strip()
        if not cid:
            return False
        return cid in self._counted_client_ids

    def record_submit(
        self,
        client_order_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        self._roll_day(now)
        cid = (client_order_id or "").strip()
        if cid and cid in self._counted_client_ids:
            return
        self._count += 1
        if cid:
            self._counted_client_ids.add(cid)

    def seed_from_ids(
        self,
        client_order_ids: set[str],
        *,
        now: datetime | None = None,
    ) -> None:
        """Restore durable same-day first-submit accounting after restart."""
        self._roll_day(now)
        for cid in client_order_ids:
            text = (cid or "").strip()
            if not text or text in self._counted_client_ids:
                continue
            self._counted_client_ids.add(text)
            self._count += 1

    def _roll_day(self, now: datetime | None) -> None:
        day = _utc_day(now)
        if self._utc_day != day:
            self._utc_day = day
            self._count = 0
            self._counted_client_ids.clear()


def _utc_day(now: datetime | None) -> str:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current.date().isoformat()


def unwrap_broker(broker: Any) -> Any:
    """Unwrap LiveCapGuardBroker decorators to the inner venue adapter."""
    current = broker
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LiveCapGuardBroker):
            current = current.inner
            continue
        break
    return current


class LiveCapGuardBroker(Broker):
    """Decorator that enforces notional + daily order caps before place_order."""

    def __init__(
        self,
        inner: Broker,
        *,
        max_order_notional: Decimal,
        max_orders_per_day: int,
        counter: LiveOrderCounter | None = None,
        ledger: JsonLiveOrderLedger | None = None,
    ) -> None:
        self._inner = inner
        self._max_order_notional = Decimal(str(max_order_notional))
        self._max_orders_per_day = int(max_orders_per_day)
        self._counter = counter if counter is not None else LiveOrderCounter()
        self._ledger = ledger
        if self._ledger is not None:
            self._seed_counter_from_ledger()

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
        cid = (request.client_order_id or "").strip() or None
        try:
            already = self._already_counted_durable(cid)
        except ConfigurationError as exc:
            return self._reject(request, message=str(exc))

        if not already and self._counter.current_count() >= self._max_orders_per_day:
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

        # Count first attempt of this client_order_id only (C1), persist to ledger.
        if not already:
            self._counter.record_submit(cid)
            if self._ledger is not None and cid:
                self._ledger.mark_first_submit_counted(cid, day=_utc_day(None))

        return self._inner.place_order(request)

    def _seed_counter_from_ledger(self) -> None:
        assert self._ledger is not None
        today = _utc_day(None)
        seeded: set[str] = set()
        for order in self._ledger.all_orders():
            if not order.first_submit_counted:
                continue
            day = (order.first_submit_day or "").strip()
            if not day:
                raise ConfigurationError(
                    "live cap accounting inconsistent: first_submit_counted=True "
                    f"without first_submit_day for {order.client_order_id}"
                )
            if day == today:
                seeded.add(order.client_order_id)
        self._counter.seed_from_ids(seeded)

    def _already_counted_durable(self, client_order_id: str | None) -> bool:
        if self._counter.already_counted(client_order_id):
            return True
        if self._ledger is None:
            return False
        cid = (client_order_id or "").strip()
        if not cid:
            # Live idempotent path always supplies an id; refuse blind counting.
            raise ConfigurationError(
                "live cap accounting fail closed: client_order_id required "
                "when LIVE order ledger is attached"
            )
        record = self._ledger.get(cid)
        if record is None:
            raise ConfigurationError(
                f"live cap accounting fail closed: ledger missing order {cid}"
            )
        if not record.first_submit_counted:
            return False
        day = (record.first_submit_day or "").strip()
        if not day:
            raise ConfigurationError(
                "live cap accounting inconsistent: first_submit_counted=True "
                f"without first_submit_day for {cid}"
            )
        today = _utc_day(None)
        if day != today:
            return False
        # Restore in-memory view so subsequent checks stay consistent.
        self._counter.seed_from_ids({cid})
        return True

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
