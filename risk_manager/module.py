"""Risk manager module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from risk_manager.rules import RiskRules


class RiskManagerModule(BaseModule):
    """Enforces risk limits and position sizing rules."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._rules: RiskRules | None = None

    @property
    def name(self) -> str:
        return "risk_manager"

    def _on_initialize(self) -> None:
        self._rules = RiskRules(self._settings)

    @property
    def rules(self) -> RiskRules:
        if self._rules is None:
            raise RuntimeError("Risk rules not initialized")
        return self._rules

    def health_check(self) -> ModuleHealth:
        if self._rules is None:
            return self._unhealthy("Risk rules not initialized")

        try:
            assessment = self._rules.assess_order(
                portfolio_value=Decimal("100000"),
                order_value=Decimal("4000"),
                open_positions=2,
            )
            return self._healthy(
                message="Risk rules operational",
                max_position_pct=str(self._settings.max_position_size_pct),
                max_daily_loss_pct=str(self._settings.max_daily_loss_pct),
                sample_approved=assessment.approved,
                sample_max_position=str(assessment.max_position_value),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
