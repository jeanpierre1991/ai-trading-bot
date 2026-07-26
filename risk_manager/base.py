"""Risk manager abstractions.

``BasicRiskManager`` is the canonical runtime evaluation path (sizing +
operational limits). ``RiskRules`` remains available for module health checks
but is not executed in parallel by the Runtime.
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
        open_positions: int = 0,
        daily_pnl_pct: Decimal | None = None,
        opens_new_exposure: bool = False,
    ) -> RiskEvaluation:
        """Return a risk evaluation for a proposed trade."""
