"""Technical analysis domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class IndicatorSource(str, Enum):
    CLOSE = "close"
    HIGH = "high"
    LOW = "low"
    VOLUME = "volume"


@dataclass(frozen=True)
class IndicatorRequest:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    source: IndicatorSource = IndicatorSource.CLOSE

    def __post_init__(self) -> None:
        normalized_name = self.name.strip().lower()
        if not normalized_name:
            raise ValueError("indicator name must not be empty")
        object.__setattr__(self, "name", normalized_name)
        if not isinstance(self.params, dict):
            raise ValueError("params must be a dictionary")


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str | None
    timeframe: str | None
    timestamp: datetime | None
    values: dict[str, Decimal | None]
    series: dict[str, list[Decimal | None]]
