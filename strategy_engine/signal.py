"""Strategy signal data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.types import SignalAction


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    action: SignalAction
    confidence: float
    strategy_name: str
    price: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str | float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str | float]:
        return {
            "symbol": self.symbol,
            "action": self.action.value,
            "confidence": self.confidence,
            "strategy_name": self.strategy_name,
            "price": float(self.price),
            "timestamp": self.timestamp.isoformat(),
            **{k: v for k, v in self.metadata.items()},
        }
