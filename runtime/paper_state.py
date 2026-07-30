"""Versioned paper-operator durable state (Milestone 12.2).

Decimal-backed values are encoded as strings so JSON round-trips do not
alter precision via binary float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, PositionId, Side, Symbol
from order_manager.manager import OrderManager, OrderRecord, OrderState
from portfolio_manager.portfolio import Portfolio, Position

OPERATOR_STATE_SCHEMA_VERSION = 1


def decimal_to_str(value: Decimal | int | str) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int) and not isinstance(value, bool):
        return format(Decimal(value), "f")
    if isinstance(value, str):
        # Validate then normalize via Decimal.
        return format(_parse_decimal(value, field_name="decimal"), "f")
    raise ConfigurationError(f"unsupported decimal encoding type: {type(value)!r}")


def _parse_decimal(raw: object, *, field_name: str) -> Decimal:
    if isinstance(raw, bool) or raw is None:
        raise ConfigurationError(f"invalid {field_name}: {raw!r}")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        raise ConfigurationError(
            f"invalid {field_name}: floats are not allowed; use string decimals"
        )
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ConfigurationError(f"invalid {field_name}: empty string")
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ConfigurationError(
                f"invalid {field_name}: {raw!r}"
            ) from exc
    raise ConfigurationError(f"invalid {field_name}: {raw!r}")


def _parse_utc_datetime(raw: object, *, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError(f"invalid {field_name}: expected ISO UTC string")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigurationError(f"invalid {field_name}: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"invalid {field_name}: timezone required ({raw!r})")
    return parsed.astimezone(timezone.utc)


def datetime_to_iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ConfigurationError("datetime must be timezone-aware UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_bar_timestamp(value: datetime) -> str:
    """Canonical idempotency key for a bar timestamp."""
    return datetime_to_iso_utc(value)


@dataclass
class OperatorState:
    """Durable paper-operator snapshot (schema v1)."""

    schema_version: int
    portfolio: dict[str, Any]
    order_manager: dict[str, Any]
    cycles_completed_total: int
    operator_start_equity: Decimal
    last_cycle_at: datetime | None
    # E1: symbol -> canonical UTC ISO of last actionable bar timestamp
    last_actionable_bar_timestamps: dict[str, str] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "portfolio": self.portfolio,
            "order_manager": self.order_manager,
            "cycles_completed_total": self.cycles_completed_total,
            "operator_start_equity": decimal_to_str(self.operator_start_equity),
            "last_cycle_at": (
                None
                if self.last_cycle_at is None
                else datetime_to_iso_utc(self.last_cycle_at)
            ),
            "last_actionable_bar_timestamps": dict(
                self.last_actionable_bar_timestamps
            ),
        }

    @classmethod
    def from_json_dict(cls, payload: Mapping[str, Any]) -> OperatorState:
        if not isinstance(payload, Mapping):
            raise ConfigurationError("operator state root must be a JSON object")
        if "schema_version" not in payload:
            raise ConfigurationError("operator state missing required field schema_version")
        version = payload["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ConfigurationError(
                f"invalid schema_version: {version!r}"
            )
        if version != OPERATOR_STATE_SCHEMA_VERSION:
            raise ConfigurationError(
                f"unsupported operator state schema_version={version}; "
                f"supported={OPERATOR_STATE_SCHEMA_VERSION}"
            )

        required = (
            "portfolio",
            "order_manager",
            "cycles_completed_total",
            "operator_start_equity",
            "last_cycle_at",
            "last_actionable_bar_timestamps",
        )
        for key in required:
            if key not in payload:
                raise ConfigurationError(
                    f"operator state missing required field {key}"
                )

        cycles = payload["cycles_completed_total"]
        if not isinstance(cycles, int) or isinstance(cycles, bool) or cycles < 0:
            raise ConfigurationError(
                f"invalid cycles_completed_total: {cycles!r}"
            )

        equity = _parse_decimal(
            payload["operator_start_equity"],
            field_name="operator_start_equity",
        )
        if equity <= 0:
            raise ConfigurationError(
                f"operator_start_equity must be positive; got {equity}"
            )

        last_cycle_raw = payload["last_cycle_at"]
        if last_cycle_raw is None:
            last_cycle_at = None
        else:
            last_cycle_at = _parse_utc_datetime(
                last_cycle_raw, field_name="last_cycle_at"
            )

        cursor_raw = payload["last_actionable_bar_timestamps"]
        if not isinstance(cursor_raw, dict):
            raise ConfigurationError(
                "last_actionable_bar_timestamps must be an object"
            )
        cursor: dict[str, str] = {}
        for symbol, ts in cursor_raw.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ConfigurationError(
                    f"invalid idempotency cursor symbol: {symbol!r}"
                )
            if not isinstance(ts, str) or not ts.strip():
                raise ConfigurationError(
                    f"invalid idempotency cursor timestamp for {symbol!r}"
                )
            # Normalize/validate timestamp format.
            cursor[symbol.strip()] = normalize_bar_timestamp(
                _parse_utc_datetime(ts, field_name="last_actionable_bar_timestamps")
            )

        portfolio = payload["portfolio"]
        order_manager = payload["order_manager"]
        if not isinstance(portfolio, dict):
            raise ConfigurationError("portfolio state must be an object")
        if not isinstance(order_manager, dict):
            raise ConfigurationError("order_manager state must be an object")
        # Validate reconstructability without mutating caller objects yet.
        validate_portfolio_snapshot(portfolio)
        validate_order_manager_snapshot(order_manager)

        return cls(
            schema_version=version,
            portfolio=dict(portfolio),
            order_manager=dict(order_manager),
            cycles_completed_total=cycles,
            operator_start_equity=equity,
            last_cycle_at=last_cycle_at,
            last_actionable_bar_timestamps=cursor,
        )


def portfolio_to_snapshot(portfolio: Portfolio) -> dict[str, Any]:
    positions: dict[str, Any] = {}
    for key, position in portfolio.positions.items():
        positions[str(key)] = {
            "position_id": str(position.position_id),
            "symbol": str(position.symbol),
            "side": position.side.value,
            "quantity": decimal_to_str(position.quantity),
            "entry_price": decimal_to_str(position.entry_price),
            "current_price": decimal_to_str(position.current_price),
            "opened_at": datetime_to_iso_utc(position.opened_at),
        }
    return {
        "cash": decimal_to_str(portfolio.cash),
        "positions": positions,
    }


def validate_portfolio_snapshot(snapshot: Mapping[str, Any]) -> None:
    if "cash" not in snapshot or "positions" not in snapshot:
        raise ConfigurationError("portfolio snapshot missing cash/positions")
    _parse_decimal(snapshot["cash"], field_name="portfolio.cash")
    positions = snapshot["positions"]
    if not isinstance(positions, dict):
        raise ConfigurationError("portfolio.positions must be an object")
    for key, raw in positions.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigurationError(f"invalid position key: {key!r}")
        if not isinstance(raw, dict):
            raise ConfigurationError(f"invalid position payload for {key!r}")
        for field_name in (
            "position_id",
            "symbol",
            "side",
            "quantity",
            "entry_price",
            "current_price",
            "opened_at",
        ):
            if field_name not in raw:
                raise ConfigurationError(
                    f"position {key!r} missing field {field_name}"
                )
        _parse_decimal(raw["quantity"], field_name=f"position[{key}].quantity")
        _parse_decimal(raw["entry_price"], field_name=f"position[{key}].entry_price")
        _parse_decimal(
            raw["current_price"], field_name=f"position[{key}].current_price"
        )
        side = raw["side"]
        if side not in {s.value for s in Side}:
            raise ConfigurationError(f"invalid position side for {key!r}: {side!r}")
        _parse_utc_datetime(raw["opened_at"], field_name=f"position[{key}].opened_at")


def apply_portfolio_snapshot(portfolio: Portfolio, snapshot: Mapping[str, Any]) -> None:
    """Replace portfolio cash/positions from a validated snapshot."""
    validate_portfolio_snapshot(snapshot)
    cash = _parse_decimal(snapshot["cash"], field_name="portfolio.cash")
    if cash < 0:
        raise ConfigurationError(f"portfolio.cash cannot be negative: {cash}")
    restored: dict[str, Position] = {}
    for key, raw in snapshot["positions"].items():
        assert isinstance(raw, dict)
        symbol = Symbol(str(raw["symbol"]))
        side = Side(str(raw["side"]))
        qty = _parse_decimal(raw["quantity"], field_name="quantity")
        if qty <= 0:
            raise ConfigurationError(f"position quantity must be positive for {key!r}")
        restored[str(key)] = Position(
            position_id=PositionId(str(raw["position_id"])),
            symbol=symbol,
            side=side,
            quantity=qty,
            entry_price=_parse_decimal(raw["entry_price"], field_name="entry_price"),
            current_price=_parse_decimal(
                raw["current_price"], field_name="current_price"
            ),
            opened_at=_parse_utc_datetime(
                raw["opened_at"], field_name="opened_at"
            ),
        )
    portfolio.cash = cash
    portfolio.positions = restored


def order_manager_to_snapshot(order_manager: OrderManager | None) -> dict[str, Any]:
    if order_manager is None:
        return {"orders": []}
    orders: list[dict[str, Any]] = []
    for record in order_manager.list_orders():
        orders.append(
            {
                "order_id": str(record.order_id),
                "symbol": str(record.symbol),
                "side": record.side.value,
                "order_type": record.order_type.value,
                "quantity": decimal_to_str(record.quantity),
                "price": (
                    None
                    if record.price is None
                    else decimal_to_str(record.price)
                ),
                "state": record.state.value,
                "created_at": datetime_to_iso_utc(record.created_at),
                "updated_at": datetime_to_iso_utc(record.updated_at),
            }
        )
    return {"orders": orders}


def validate_order_manager_snapshot(snapshot: Mapping[str, Any]) -> None:
    if "orders" not in snapshot:
        raise ConfigurationError("order_manager snapshot missing orders")
    orders = snapshot["orders"]
    if not isinstance(orders, list):
        raise ConfigurationError("order_manager.orders must be a list")
    for index, raw in enumerate(orders):
        if not isinstance(raw, dict):
            raise ConfigurationError(f"order_manager.orders[{index}] must be an object")
        for field_name in (
            "order_id",
            "symbol",
            "side",
            "order_type",
            "quantity",
            "price",
            "state",
            "created_at",
            "updated_at",
        ):
            if field_name not in raw:
                raise ConfigurationError(
                    f"order_manager.orders[{index}] missing field {field_name}"
                )
        _parse_decimal(raw["quantity"], field_name=f"orders[{index}].quantity")
        if raw["price"] is not None:
            _parse_decimal(raw["price"], field_name=f"orders[{index}].price")
        if raw["side"] not in {s.value for s in Side}:
            raise ConfigurationError(f"invalid order side at [{index}]: {raw['side']!r}")
        if raw["order_type"] not in {t.value for t in OrderType}:
            raise ConfigurationError(
                f"invalid order_type at [{index}]: {raw['order_type']!r}"
            )
        if raw["state"] not in {s.value for s in OrderState}:
            raise ConfigurationError(
                f"invalid order state at [{index}]: {raw['state']!r}"
            )
        _parse_utc_datetime(raw["created_at"], field_name=f"orders[{index}].created_at")
        _parse_utc_datetime(raw["updated_at"], field_name=f"orders[{index}].updated_at")


def apply_order_manager_snapshot(
    order_manager: OrderManager,
    snapshot: Mapping[str, Any],
) -> None:
    """Replace OrderManager contents from a validated snapshot."""
    validate_order_manager_snapshot(snapshot)
    restored: dict[str, OrderRecord] = {}
    for raw in snapshot["orders"]:
        assert isinstance(raw, dict)
        order_id = str(raw["order_id"])
        price_raw = raw["price"]
        record = OrderRecord(
            order_id=OrderId(order_id),
            symbol=Symbol(str(raw["symbol"])),
            side=Side(str(raw["side"])),
            order_type=OrderType(str(raw["order_type"])),
            quantity=_parse_decimal(raw["quantity"], field_name="quantity"),
            price=(
                None
                if price_raw is None
                else _parse_decimal(price_raw, field_name="price")
            ),
            state=OrderState(str(raw["state"])),
            created_at=_parse_utc_datetime(
                raw["created_at"], field_name="created_at"
            ),
            updated_at=_parse_utc_datetime(
                raw["updated_at"], field_name="updated_at"
            ),
        )
        restored[order_id] = record
    order_manager.replace_all(restored)
