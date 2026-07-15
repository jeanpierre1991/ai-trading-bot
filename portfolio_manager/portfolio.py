"""Portfolio and position data structures."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.types import PositionId, Side, Symbol

_MONEY = Decimal("0.01")
_QTY = Decimal("0.0001")


@dataclass(frozen=True)
class Fill:
    """Immutable fill payload to be applied to a portfolio."""

    symbol: Symbol
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")


@dataclass
class Position:
    position_id: PositionId
    symbol: Symbol
    side: Side
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def market_value(self) -> Decimal:
        return (self.quantity * self.current_price).quantize(_MONEY)

    @property
    def unrealized_pnl(self) -> Decimal:
        direction = Decimal("1") if self.side == Side.BUY else Decimal("-1")
        pnl = (self.current_price - self.entry_price) * self.quantity * direction
        return pnl.quantize(_MONEY)

    def to_dict(self) -> dict[str, str | float]:
        return {
            "position_id": str(self.position_id),
            "symbol": str(self.symbol),
            "side": self.side.value,
            "quantity": float(self.quantity),
            "entry_price": float(self.entry_price),
            "current_price": float(self.current_price),
            "market_value": float(self.market_value),
            "unrealized_pnl": float(self.unrealized_pnl),
        }


@dataclass
class Portfolio:
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def positions_value(self) -> Decimal:
        return sum((p.market_value for p in self.positions.values()), Decimal("0"))

    @property
    def total_value(self) -> Decimal:
        return (self.cash + self.positions_value).quantize(_MONEY)

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def summary(self) -> dict[str, float | int]:
        return {
            "cash": float(self.cash),
            "positions_value": float(self.positions_value),
            "total_value": float(self.total_value),
            "position_count": self.position_count,
        }

    def apply_fill(self, fill: Fill) -> None:
        """Apply a fill to cash and long positions.

        BUY debits cash and opens/increases a long position.
        SELL credits cash and reduces/closes a long position.
        """
        self._validate_fill(fill)
        key = str(fill.symbol)

        if fill.side is Side.BUY:
            self._apply_buy(key, fill)
        elif fill.side is Side.SELL:
            self._apply_sell(key, fill)
        else:  # pragma: no cover - Side currently only BUY/SELL
            raise ValueError(f"Unsupported fill side: {fill.side}")

    @staticmethod
    def _validate_fill(fill: Fill) -> None:
        if fill.quantity <= 0:
            raise ValueError("Fill quantity must be positive")
        if fill.price < 0:
            raise ValueError("Fill price cannot be negative")
        if fill.fee < 0:
            raise ValueError("Fill fee cannot be negative")

    def _apply_buy(self, key: str, fill: Fill) -> None:
        notional = (fill.quantity * fill.price).quantize(_MONEY)
        total_debit = (notional + fill.fee).quantize(_MONEY)
        if total_debit > self.cash:
            raise ValueError(
                f"Insufficient cash for buy: need {total_debit}, available {self.cash}"
            )

        existing = self.positions.get(key)
        if existing is not None and existing.side is not Side.BUY:
            raise ValueError(f"Cannot buy into non-long position for {key}")

        self.cash = (self.cash - total_debit).quantize(_MONEY)

        if existing is None:
            self.positions[key] = Position(
                position_id=PositionId(str(uuid.uuid4())),
                symbol=fill.symbol,
                side=Side.BUY,
                quantity=fill.quantity,
                entry_price=fill.price,
                current_price=fill.price,
            )
            return

        total_qty = existing.quantity + fill.quantity
        weighted_entry = (
            (existing.quantity * existing.entry_price) + (fill.quantity * fill.price)
        ) / total_qty
        existing.quantity = total_qty
        existing.entry_price = weighted_entry.quantize(_QTY)
        existing.current_price = fill.price

    def _apply_sell(self, key: str, fill: Fill) -> None:
        existing = self.positions.get(key)
        if existing is None:
            raise ValueError(f"Cannot sell {key}: no open position")
        if existing.side is not Side.BUY:
            raise ValueError(f"Cannot sell non-long position for {key}")
        if fill.quantity > existing.quantity:
            raise ValueError(
                f"Oversell {key}: requested {fill.quantity}, available {existing.quantity}"
            )

        notional = (fill.quantity * fill.price).quantize(_MONEY)
        total_credit = (notional - fill.fee).quantize(_MONEY)
        if total_credit < 0:
            raise ValueError("Fill fee exceeds sell proceeds")

        self.cash = (self.cash + total_credit).quantize(_MONEY)

        remaining = existing.quantity - fill.quantity
        if remaining == 0:
            del self.positions[key]
            return

        existing.quantity = remaining
        existing.current_price = fill.price
