"""Abort-only live reconciliation (Milestone 13.3).

OBSERVE → DIFF → POLICY. Never mutates portfolio, never cancels broker orders,
never invents compensating trades (Decision L4 / R3).

May persist ledger-only transitions that resolve crash/UNKNOWN windows when the
broker-agnostic observe contract positively proves absence (FAILED_ABSENT) or
adopts a confirmed pre-ack broker order into a non-fill local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from broker_interface.reconcile_port import ReconcileCapableBroker
from broker_interface.snapshots import BrokerOrderSnapshot, BrokerOrderStatus
from core.exceptions import ConfigurationError
from runtime.live_caps import unwrap_broker
from runtime.live_order_ledger import (
    JsonLiveOrderLedger,
    LiveOrderState,
    parse_ledger_quantity,
)

# R2: relative epsilon for average entry comparison.
DEFAULT_ENTRY_PRICE_EPSILON = Decimal("0.0001")

_LOCAL_OPEN = frozenset(
    {
        LiveOrderState.CREATED.value,
        LiveOrderState.SUBMITTING.value,
        LiveOrderState.SUBMITTED.value,
        LiveOrderState.ACCEPTED_OPEN.value,
        LiveOrderState.PARTIALLY_FILLED.value,
        LiveOrderState.UNKNOWN.value,
    }
)

_PRE_ACK = frozenset(
    {
        LiveOrderState.CREATED.value,
        LiveOrderState.SUBMITTING.value,
    }
)


class ReconcileMismatchCode(str, Enum):
    MATCH = "MATCH"
    LOCAL_OPEN_MISSING_AT_BROKER = "LOCAL_OPEN_MISSING_AT_BROKER"
    BROKER_OPEN_UNEXPECTED = "BROKER_OPEN_UNEXPECTED"
    ORDER_STATUS_MISMATCH = "ORDER_STATUS_MISMATCH"
    ORDER_QTY_MISMATCH = "ORDER_QTY_MISMATCH"
    POSITION_QTY_MISMATCH = "POSITION_QTY_MISMATCH"
    POSITION_ENTRY_MISMATCH = "POSITION_ENTRY_MISMATCH"
    UNKNOWN_LOCAL_ORDER = "UNKNOWN_LOCAL_ORDER"
    BROKER_FILLED_LOCAL_NOT_BOOKED = "BROKER_FILLED_LOCAL_NOT_BOOKED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    MALFORMED_BROKER_PAYLOAD = "MALFORMED_BROKER_PAYLOAD"
    NOT_RECONCILE_CAPABLE = "NOT_RECONCILE_CAPABLE"


class ReconcilePolicy(str, Enum):
    PROCEED = "PROCEED"
    ABORT = "ABORT"


@dataclass(frozen=True)
class ReconcileDecision:
    policy: ReconcilePolicy
    code: ReconcileMismatchCode
    reason: str


def reconcile_live(
    *,
    ledger: JsonLiveOrderLedger,
    broker: Any,
    portfolio: Any,
    entry_price_epsilon: Decimal = DEFAULT_ENTRY_PRICE_EPSILON,
) -> ReconcileDecision:
    """Compare local ledger/portfolio to broker observations; abort on mismatch."""
    capable = _as_reconcile_broker(broker)
    if capable is None:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.NOT_RECONCILE_CAPABLE,
            reason="live broker does not implement ReconcileCapableBroker",
        )

    recovery = _resolve_unknown_and_pre_ack(ledger=ledger, capable=capable)
    if recovery is not None:
        return recovery

    try:
        open_orders = capable.list_open_orders()
        positions = capable.list_positions()
    except ConfigurationError as exc:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
            reason=f"malformed broker reconciliation response: {exc}",
        )
    except Exception as exc:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.BROKER_UNAVAILABLE,
            reason=f"broker unavailable during reconciliation: {exc.__class__.__name__}",
        )

    if not isinstance(open_orders, list) or not isinstance(positions, list):
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
            reason="broker open orders/positions response must be lists",
        )

    local_by_client = {o.client_order_id: o for o in ledger.all_orders()}
    local_open = [o for o in local_by_client.values() if o.state in _LOCAL_OPEN]

    # Unexpected broker open orders (not in local ledger).
    for snap in open_orders:
        cid = (snap.client_order_id or "").strip()
        if not cid or cid not in local_by_client:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_OPEN_UNEXPECTED,
                reason=(
                    "unexpected broker open order "
                    f"broker_order_id={snap.broker_order_id!r} "
                    f"client_order_id={cid!r}"
                ),
            )

    # Local open missing / qty mismatch / broker filled but local not booked.
    for local in local_open:
        if local.state in _PRE_ACK:
            # Pre-ack rows are resolved in _resolve_unknown_and_pre_ack.
            continue
        if local.state == LiveOrderState.UNKNOWN.value:
            # UNKNOWN rows are resolved or aborted earlier.
            continue
        try:
            snap = capable.get_order_by_client_id(local.client_order_id)
        except ConfigurationError as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
                reason=f"malformed broker reconciliation response: {exc}",
            )
        except Exception as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_UNAVAILABLE,
                reason=f"broker unavailable during reconciliation: {exc.__class__.__name__}",
            )

        if snap is None:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.LOCAL_OPEN_MISSING_AT_BROKER,
                reason=(
                    f"local open/submitted order {local.client_order_id} "
                    "missing at broker"
                ),
            )

        local_qty = parse_ledger_quantity(local.quantity)
        if snap.quantity != local_qty or snap.side.value != local.side:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.ORDER_QTY_MISMATCH,
                reason=(
                    f"order qty/side mismatch for {local.client_order_id}: "
                    f"local={local.side}/{local_qty} broker={snap.side.value}/{snap.quantity}"
                ),
            )

        if snap.status is BrokerOrderStatus.FILLED and local.state != LiveOrderState.FILLED.value:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_FILLED_LOCAL_NOT_BOOKED,
                reason=(
                    f"broker FILLED {local.client_order_id} but local ledger "
                    f"state={local.state}; abort-only (R3)"
                ),
            )

        if (
            snap.status is BrokerOrderStatus.REJECTED
            and local.state
            in {
                LiveOrderState.SUBMITTED.value,
                LiveOrderState.ACCEPTED_OPEN.value,
                LiveOrderState.PARTIALLY_FILLED.value,
            }
        ):
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.ORDER_STATUS_MISMATCH,
                reason=(
                    f"order status mismatch for {local.client_order_id}: "
                    f"local={local.state} broker={snap.status.value}"
                ),
            )

    # Positions (mandatory R1). Cash not compared in M13.3.
    local_positions = _local_positions(portfolio)
    broker_map = {
        str(p.symbol).strip().upper(): p for p in positions if p.quantity != 0
    }
    symbols = set(local_positions) | set(broker_map)
    for symbol in symbols:
        local_qty, local_entry = local_positions.get(symbol, (Decimal("0"), None))
        broker_pos = broker_map.get(symbol)
        broker_qty = broker_pos.quantity if broker_pos is not None else Decimal("0")
        if local_qty != broker_qty:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.POSITION_QTY_MISMATCH,
                reason=(
                    f"position qty mismatch for {symbol}: "
                    f"local={local_qty} broker={broker_qty}"
                ),
            )
        if (
            local_qty != 0
            and local_entry is not None
            and broker_pos is not None
            and broker_pos.avg_entry_price is not None
        ):
            if not _entry_within_epsilon(
                local_entry,
                broker_pos.avg_entry_price,
                epsilon=entry_price_epsilon,
            ):
                return ReconcileDecision(
                    policy=ReconcilePolicy.ABORT,
                    code=ReconcileMismatchCode.POSITION_ENTRY_MISMATCH,
                    reason=(
                        f"position avg entry mismatch for {symbol}: "
                        f"local={local_entry} broker={broker_pos.avg_entry_price}"
                    ),
                )

    return ReconcileDecision(
        policy=ReconcilePolicy.PROCEED,
        code=ReconcileMismatchCode.MATCH,
        reason="reconcile match",
    )


def _resolve_unknown_and_pre_ack(
    *,
    ledger: JsonLiveOrderLedger,
    capable: ReconcileCapableBroker,
) -> ReconcileDecision | None:
    """Resolve UNKNOWN / CREATED / SUBMITTING via observe-by-client-id.

    Returns a decision when reconciliation must ABORT immediately; otherwise
    persists safe ledger transitions and returns None to continue.
    """
    unknowns = [
        o
        for o in ledger.all_orders()
        if o.state == LiveOrderState.UNKNOWN.value
    ]
    for local in unknowns:
        try:
            snap = capable.get_order_by_client_id(local.client_order_id)
        except ConfigurationError as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
                reason=f"malformed broker reconciliation response: {exc}",
            )
        except Exception as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_UNAVAILABLE,
                reason=f"broker unavailable during reconciliation: {exc.__class__.__name__}",
            )

        if snap is None:
            # Confirmed absence via broker-agnostic contract (None / not found).
            ledger.update_state(
                local.client_order_id,
                LiveOrderState.FAILED_ABSENT,
                clear_last_error=True,
            )
            continue

        # Any positive observation while UNKNOWN cannot invent local repair.
        if snap.status is BrokerOrderStatus.FILLED:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_FILLED_LOCAL_NOT_BOOKED,
                reason=(
                    f"broker FILLED client_order_id={local.client_order_id} but "
                    "local state is UNKNOWN/unbooked; abort-only (R3)"
                ),
            )
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.UNKNOWN_LOCAL_ORDER,
            reason=(
                f"local order {local.client_order_id} remains UNKNOWN after broker "
                f"observe (status={snap.status.value}); fail closed"
            ),
        )

    pre_ack = [o for o in ledger.all_orders() if o.state in _PRE_ACK]
    for local in pre_ack:
        try:
            snap = capable.get_order_by_client_id(local.client_order_id)
        except ConfigurationError as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
                reason=f"malformed broker reconciliation response: {exc}",
            )
        except Exception as exc:
            return ReconcileDecision(
                policy=ReconcilePolicy.ABORT,
                code=ReconcileMismatchCode.BROKER_UNAVAILABLE,
                reason=f"broker unavailable during reconciliation: {exc.__class__.__name__}",
            )

        if snap is None:
            ledger.update_state(
                local.client_order_id,
                LiveOrderState.FAILED_ABSENT,
                clear_last_error=True,
            )
            continue

        adopted = _adopt_pre_ack_observation(ledger, local.client_order_id, snap)
        if adopted is not None:
            return adopted

    return None


def _adopt_pre_ack_observation(
    ledger: JsonLiveOrderLedger,
    client_order_id: str,
    snap: BrokerOrderSnapshot,
) -> ReconcileDecision | None:
    """Adopt a confirmed broker order for CREATED/SUBMITTING without portfolio repair."""
    local = ledger.get(client_order_id)
    if local is None:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.MALFORMED_BROKER_PAYLOAD,
            reason=f"ledger missing {client_order_id} during pre-ack adopt",
        )

    local_qty = parse_ledger_quantity(local.quantity)
    if snap.quantity != local_qty or snap.side.value != local.side:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.ORDER_QTY_MISMATCH,
            reason=(
                f"order qty/side mismatch for {client_order_id}: "
                f"local={local.side}/{local_qty} broker={snap.side.value}/{snap.quantity}"
            ),
        )

    broker_oid = str(snap.broker_order_id)

    if snap.status is BrokerOrderStatus.FILLED:
        return ReconcileDecision(
            policy=ReconcilePolicy.ABORT,
            code=ReconcileMismatchCode.BROKER_FILLED_LOCAL_NOT_BOOKED,
            reason=(
                f"broker FILLED {client_order_id} while local was CREATED/SUBMITTING; "
                "abort-only (R3), no automatic booking"
            ),
        )

    if snap.status is BrokerOrderStatus.OPEN:
        ledger.update_state(
            client_order_id,
            LiveOrderState.ACCEPTED_OPEN,
            broker_order_id=broker_oid,
            clear_last_error=True,
        )
        return None

    if snap.status is BrokerOrderStatus.PARTIALLY_FILLED:
        ledger.update_state(
            client_order_id,
            LiveOrderState.PARTIALLY_FILLED,
            broker_order_id=broker_oid,
            filled_quantity=snap.filled_quantity,
            avg_fill_price=snap.avg_fill_price,
            clear_last_error=True,
        )
        return None

    if snap.status is BrokerOrderStatus.REJECTED:
        ledger.update_state(
            client_order_id,
            LiveOrderState.REJECTED,
            broker_order_id=broker_oid,
            clear_last_error=True,
        )
        return None

    if snap.status is BrokerOrderStatus.CANCELED:
        ledger.update_state(
            client_order_id,
            LiveOrderState.CANCELED,
            broker_order_id=broker_oid,
            clear_last_error=True,
        )
        return None

    return ReconcileDecision(
        policy=ReconcilePolicy.ABORT,
        code=ReconcileMismatchCode.ORDER_STATUS_MISMATCH,
        reason=(
            f"ambiguous broker status for pre-ack order {client_order_id}: "
            f"{snap.status.value}"
        ),
    )


def _as_reconcile_broker(broker: Any) -> ReconcileCapableBroker | None:
    inner = unwrap_broker(broker)
    if isinstance(inner, ReconcileCapableBroker):
        return inner
    return None


def _local_positions(portfolio: Any) -> dict[str, tuple[Decimal, Decimal | None]]:
    result: dict[str, tuple[Decimal, Decimal | None]] = {}
    positions = getattr(portfolio, "positions", None)
    if not isinstance(positions, dict):
        return result
    for key, pos in positions.items():
        symbol = str(getattr(pos, "symbol", key)).strip().upper()
        qty = Decimal(str(getattr(pos, "quantity", "0")))
        if qty == 0:
            continue
        entry_raw = getattr(pos, "entry_price", None)
        entry = Decimal(str(entry_raw)) if entry_raw is not None else None
        result[symbol] = (qty, entry)
    return result


def _entry_within_epsilon(
    local: Decimal,
    broker: Decimal,
    *,
    epsilon: Decimal,
) -> bool:
    if local == broker:
        return True
    scale = max(abs(local), abs(broker), Decimal("1"))
    return abs(local - broker) <= (epsilon * scale)
