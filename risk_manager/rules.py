"""Risk management rules and assessments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from config.settings import Settings
from core.exceptions import RiskViolationError


@dataclass(frozen=True)
class RiskAssessment:
    approved: bool
    max_position_value: Decimal
    reason: str
    checks_passed: list[str]
    checks_failed: list[str]


class RiskRules:
    """Evaluates trades against configured risk parameters."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def assess_order(
        self,
        portfolio_value: Decimal,
        order_value: Decimal,
        open_positions: int,
        daily_pnl_pct: Decimal = Decimal("0"),
    ) -> RiskAssessment:
        passed: list[str] = []
        failed: list[str] = []

        max_position = portfolio_value * self._settings.max_position_size_pct
        if order_value <= max_position:
            passed.append("position_size")
        else:
            failed.append("position_size")

        if open_positions < self._settings.max_open_positions:
            passed.append("open_positions")
        else:
            failed.append("open_positions")

        if daily_pnl_pct >= -self._settings.max_daily_loss_pct:
            passed.append("daily_loss")
        else:
            failed.append("daily_loss")

        approved = len(failed) == 0
        reason = "All risk checks passed" if approved else f"Failed checks: {', '.join(failed)}"

        return RiskAssessment(
            approved=approved,
            max_position_value=max_position.quantize(Decimal("0.01")),
            reason=reason,
            checks_passed=passed,
            checks_failed=failed,
        )

    def validate_or_raise(self, assessment: RiskAssessment) -> None:
        if not assessment.approved:
            raise RiskViolationError(assessment.reason)
