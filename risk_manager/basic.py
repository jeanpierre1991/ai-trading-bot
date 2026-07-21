"""Basic risk manager implementation (Milestone 4).

Not wired into the execution pipeline yet. Uses existing Settings for
position sizing and documented constants for stop-loss / risk-reward.
"""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from risk_manager.base import RiskManager
from risk_manager.models import RiskEvaluation

# Documented defaults until dedicated Settings fields exist.
DEFAULT_STOP_LOSS_PCT = Decimal("0.02")  # 2% below entry
DEFAULT_RISK_REWARD_RATIO = Decimal("2")  # take-profit = 2x risk distance

_MONEY = Decimal("0.01")
_PRICE = Decimal("0.0001")


class BasicRiskManager(RiskManager):
    """Simple risk evaluation: size from Settings, fixed SL%, RR multiple for TP."""

    def __init__(
        self,
        settings: Settings,
        *,
        stop_loss_pct: Decimal = DEFAULT_STOP_LOSS_PCT,
        risk_reward_ratio: Decimal = DEFAULT_RISK_REWARD_RATIO,
    ) -> None:
        if stop_loss_pct <= 0 or stop_loss_pct >= 1:
            raise ValueError("stop_loss_pct must be between 0 and 1 (exclusive)")
        if risk_reward_ratio <= 0:
            raise ValueError("risk_reward_ratio must be positive")

        self._settings = settings
        self._stop_loss_pct = stop_loss_pct
        self._risk_reward_ratio = risk_reward_ratio

    def evaluate(
        self,
        *,
        symbol: str,
        entry_price: Decimal,
        portfolio_value: Decimal,
    ) -> RiskEvaluation:
        if portfolio_value <= 0:
            return RiskEvaluation(
                approved=False,
                reason="Portfolio value must be positive",
                position_size=Decimal("0"),
                stop_loss=None,
                take_profit=None,
            )

        if entry_price <= 0:
            return RiskEvaluation(
                approved=False,
                reason="Entry price must be positive",
                position_size=Decimal("0"),
                stop_loss=None,
                take_profit=None,
            )

        position_size = (portfolio_value * self._settings.max_position_size_pct).quantize(
            _MONEY
        )
        stop_loss = (entry_price * (Decimal("1") - self._stop_loss_pct)).quantize(_PRICE)
        risk_distance = entry_price - stop_loss
        take_profit = (entry_price + risk_distance * self._risk_reward_ratio).quantize(
            _PRICE
        )

        return RiskEvaluation(
            approved=True,
            reason=f"Approved for {symbol}: size within max_position_size_pct",
            position_size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
