"""Portfolio and position data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.types import PositionId, Side, Symbol


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
        return (self.quantity * self.current_price).quantize(Decimal("0.01"))

    @property
    def unrealized_pnl(self) -> Decimal:
        direction = Decimal("1") if self.side == Side.BUY else Decimal("-1")
        pnl = (self.current_price - self.entry_price) * self.quantity * direction
        return pnl.quantize(Decimal("0.01"))

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
        return (self.cash + self.positions_value).quantize(Decimal("0.01"))

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
