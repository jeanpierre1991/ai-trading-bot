"""Atomic versioned JSON live order ledger (Milestone 13.3, Decision P1/P2).

Separate from M12 paper OperatorState / OrderManager. Fail closed on corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from core.exceptions import ConfigurationError
from core.types import OrderType, Side, Symbol

LIVE_LEDGER_SCHEMA_VERSION = 1

_OPEN_STATES_FOR_SYMBOL_LIMIT = frozenset(
    {
        "created",
        "submitting",
        "submitted",
        "accepted_open",
        "partially_filled",
        "unknown",
    }
)

class LiveOrderState(str, Enum):
    CREATED = "created"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    ACCEPTED_OPEN = "accepted_open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    FAILED_ABSENT = "failed_absent"


@dataclass
class LiveOrderRecord:
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    state: str
    broker_order_id: str | None = None
    filled_quantity: str = "0"
    avg_fill_price: str | None = None
    first_submit_counted: bool = False
    first_submit_day: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "state": self.state,
            "broker_order_id": self.broker_order_id,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "first_submit_counted": self.first_submit_counted,
            "first_submit_day": self.first_submit_day,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> LiveOrderRecord:
        if not isinstance(raw, dict):
            raise ConfigurationError("live ledger order entry must be an object")
        cid = str(raw.get("client_order_id") or "").strip()
        if not cid:
            raise ConfigurationError("live ledger order missing client_order_id")
        counted = bool(raw.get("first_submit_counted", False))
        day = _optional_str(raw.get("first_submit_day"))
        if counted and not day:
            raise ConfigurationError(
                f"live ledger order {cid} has first_submit_counted without "
                "first_submit_day"
            )
        return cls(
            client_order_id=cid,
            symbol=str(raw.get("symbol") or "").strip(),
            side=str(raw.get("side") or "").strip(),
            order_type=str(raw.get("order_type") or "").strip(),
            quantity=str(raw.get("quantity") or "").strip(),
            state=str(raw.get("state") or "").strip(),
            broker_order_id=_optional_str(raw.get("broker_order_id")),
            filled_quantity=str(raw.get("filled_quantity") or "0"),
            avg_fill_price=_optional_str(raw.get("avg_fill_price")),
            first_submit_counted=counted,
            first_submit_day=day,
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            last_error=_optional_str(raw.get("last_error")),
        )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class LiveOrderLedgerData:
    schema_version: int
    orders: dict[str, LiveOrderRecord] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "orders": {k: v.to_dict() for k, v in self.orders.items()},
        }


class JsonLiveOrderLedger:
    """Atomic JSON ledger using temp-file write + ``os.replace``."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._data = LiveOrderLedgerData(schema_version=LIVE_LEDGER_SCHEMA_VERSION)

    @property
    def path(self) -> Path:
        return self._path

    def ensure_ready(self) -> None:
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"failed to create live ledger directory {parent}: {exc}"
            ) from exc
        if self._path.is_file():
            self.load()
        else:
            self.save()

    def load(self) -> LiveOrderLedgerData:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"failed to read live order ledger {self._path}: {exc}"
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"live order ledger is not valid JSON: {self._path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ConfigurationError("live order ledger root must be an object")
        version = payload.get("schema_version")
        if version != LIVE_LEDGER_SCHEMA_VERSION:
            raise ConfigurationError(
                f"unsupported live ledger schema_version={version!r}; "
                f"expected {LIVE_LEDGER_SCHEMA_VERSION}"
            )
        orders_raw = payload.get("orders")
        if not isinstance(orders_raw, dict):
            raise ConfigurationError("live ledger orders must be an object")
        orders: dict[str, LiveOrderRecord] = {}
        for key, value in orders_raw.items():
            record = LiveOrderRecord.from_dict(value)
            if record.client_order_id != str(key):
                raise ConfigurationError(
                    "live ledger order key must match client_order_id"
                )
            orders[record.client_order_id] = record
        self._data = LiveOrderLedgerData(
            schema_version=LIVE_LEDGER_SCHEMA_VERSION,
            orders=orders,
        )
        return self._data

    def save(self) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data.to_json_dict(), indent=2, sort_keys=True)
        fd: int | None = None
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
        except OSError as exc:
            raise ConfigurationError(
                f"failed to persist live order ledger {self._path}: {exc}"
            ) from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def all_orders(self) -> list[LiveOrderRecord]:
        return list(self._data.orders.values())

    def get(self, client_order_id: str) -> LiveOrderRecord | None:
        return self._data.orders.get(client_order_id)

    def has_blocking_unknown(self) -> bool:
        return any(
            o.state == LiveOrderState.UNKNOWN.value for o in self._data.orders.values()
        )

    def orders_in_states(self, *states: str) -> list[LiveOrderRecord]:
        wanted = {str(s) for s in states}
        return [o for o in self._data.orders.values() if o.state in wanted]

    def open_order_for_symbol(self, symbol: str) -> LiveOrderRecord | None:
        key = str(symbol).strip().upper()
        for order in self._data.orders.values():
            if order.symbol.upper() != key:
                continue
            if order.state in _OPEN_STATES_FOR_SYMBOL_LIMIT:
                return order
        return None

    def retryable_order_for_symbol(self, symbol: str) -> LiveOrderRecord | None:
        """Return FAILED_ABSENT logical order for symbol (same-id retry candidate)."""
        key = str(symbol).strip().upper()
        for order in self._data.orders.values():
            if order.symbol.upper() != key:
                continue
            if order.state == LiveOrderState.FAILED_ABSENT.value:
                return order
        return None

    def create_order(
        self,
        *,
        symbol: Symbol,
        side: Side,
        order_type: OrderType,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> LiveOrderRecord:
        cid = (client_order_id or str(uuid.uuid4())).strip()
        if not cid:
            raise ConfigurationError("client_order_id must be non-empty")
        if cid in self._data.orders:
            raise ConfigurationError(f"duplicate client_order_id in ledger: {cid}")
        now = _utc_now_iso()
        record = LiveOrderRecord(
            client_order_id=cid,
            symbol=str(symbol).strip().upper(),
            side=side.value if isinstance(side, Side) else str(side),
            order_type=(
                order_type.value if isinstance(order_type, OrderType) else str(order_type)
            ),
            quantity=format(Decimal(str(quantity)), "f"),
            state=LiveOrderState.CREATED.value,
            created_at=now,
            updated_at=now,
        )
        self._data.orders[cid] = record
        self.save()
        return record

    def prepare_retry(
        self,
        client_order_id: str,
        *,
        side: Side,
        order_type: OrderType,
        quantity: Decimal,
    ) -> LiveOrderRecord:
        """Reuse an existing FAILED_ABSENT logical order for a supervised retry."""
        record = self._data.orders.get(client_order_id)
        if record is None:
            raise ConfigurationError(
                f"live ledger has no order {client_order_id!r}"
            )
        if record.state != LiveOrderState.FAILED_ABSENT.value:
            raise ConfigurationError(
                f"live ledger order {client_order_id!r} is not FAILED_ABSENT "
                f"(state={record.state})"
            )
        record.side = side.value if isinstance(side, Side) else str(side)
        record.order_type = (
            order_type.value if isinstance(order_type, OrderType) else str(order_type)
        )
        record.quantity = format(Decimal(str(quantity)), "f")
        record.state = LiveOrderState.SUBMITTING.value
        record.updated_at = _utc_now_iso()
        record.last_error = None
        self.save()
        return record

    def update_state(
        self,
        client_order_id: str,
        state: LiveOrderState,
        *,
        broker_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
        avg_fill_price: Decimal | None = None,
        first_submit_counted: bool | None = None,
        first_submit_day: str | None = None,
        last_error: str | None = None,
        clear_last_error: bool = False,
    ) -> LiveOrderRecord:
        record = self._data.orders.get(client_order_id)
        if record is None:
            raise ConfigurationError(
                f"live ledger has no order {client_order_id!r}"
            )
        record.state = state.value
        record.updated_at = _utc_now_iso()
        if broker_order_id is not None:
            record.broker_order_id = broker_order_id
        if filled_quantity is not None:
            record.filled_quantity = format(Decimal(str(filled_quantity)), "f")
        if avg_fill_price is not None:
            record.avg_fill_price = format(Decimal(str(avg_fill_price)), "f")
        if first_submit_counted is not None:
            record.first_submit_counted = first_submit_counted
        if first_submit_day is not None:
            record.first_submit_day = first_submit_day
        if clear_last_error:
            record.last_error = None
        elif last_error is not None:
            record.last_error = last_error
        if record.first_submit_counted and not (record.first_submit_day or "").strip():
            raise ConfigurationError(
                f"live ledger order {client_order_id!r} inconsistent: "
                "first_submit_counted without first_submit_day"
            )
        self.save()
        return record

    def mark_first_submit_counted(
        self, client_order_id: str, *, day: str
    ) -> LiveOrderRecord:
        record = self._data.orders.get(client_order_id)
        if record is None:
            raise ConfigurationError(
                f"live ledger has no order {client_order_id!r}"
            )
        day_text = str(day or "").strip()
        if not day_text:
            raise ConfigurationError("first_submit_day must be non-empty")
        if record.first_submit_counted:
            existing = (record.first_submit_day or "").strip()
            if existing and existing != day_text:
                raise ConfigurationError(
                    f"live cap accounting inconsistent for {client_order_id}: "
                    f"first_submit_day={existing} vs {day_text}"
                )
            if not existing:
                record.first_submit_day = day_text
                record.updated_at = _utc_now_iso()
                self.save()
            return record
        record.first_submit_counted = True
        record.first_submit_day = day_text
        record.updated_at = _utc_now_iso()
        self.save()
        return record

    def counted_client_order_ids_for_day(self, day: str) -> set[str]:
        day_text = str(day or "").strip()
        return {
            o.client_order_id
            for o in self._data.orders.values()
            if o.first_submit_counted and (o.first_submit_day or "").strip() == day_text
        }


def parse_ledger_quantity(raw: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(f"invalid ledger quantity: {raw!r}") from exc
