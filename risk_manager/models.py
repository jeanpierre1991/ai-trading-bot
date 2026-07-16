"""Risk evaluation domain models for Milestone 4."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskEvaluation:
    """Minimal outcome of a risk assessment for a proposed trade."""

    approved: bool
    reason: str
    position_size: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
