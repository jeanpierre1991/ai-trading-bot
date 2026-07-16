"""Risk manager abstractions (Milestone 4 structure).

This contract is not wired into the execution pipeline yet.
Existing ``RiskRules`` / ``RiskAssessment`` remain the production path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from risk_manager.models import RiskEvaluation


class RiskManager(ABC):
    """Contract for evaluating trade risk before execution."""

    @abstractmethod
    def evaluate(
        self,
        *,
        symbol: str,
        entry_price: Decimal,
        portfolio_value: Decimal,
    ) -> RiskEvaluation:
        """Return a risk evaluation for a proposed trade."""
