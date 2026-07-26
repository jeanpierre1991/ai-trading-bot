"""Basic risk manager implementation (canonical Runtime path).

Combines Milestone 4/7 position sizing and SL/TP with Milestone 8.2
operational limits (``max_open_positions``, ``max_daily_loss_pct``).
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
    """Size from Settings, fixed SL%/RR, plus operational open-position/daily-loss gates."""

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
        open_positions: int = 0,
        daily_pnl_pct: Decimal | None = None,
        opens_new_exposure: bool = False,
    ) -> RiskEvaluation:
        if not isinstance(symbol, str) or not symbol.strip():
            return self._reject("Symbol must be a non-empty string")

        if portfolio_value <= 0:
            return self._reject("Portfolio value must be positive")

        if entry_price <= 0:
            return self._reject("Entry price must be positive")

        max_position_pct = self._settings.max_position_size_pct
        if max_position_pct <= 0:
            return self._reject("max_position_size_pct must be positive")
        if max_position_pct > 1:
            return self._reject("max_position_size_pct cannot exceed 1 (100%)")

        daily_loss_limit = self._settings.max_daily_loss_pct
        if daily_loss_limit > 0:
            if daily_pnl_pct is None:
                return self._reject(
                    "daily_pnl_pct is required to evaluate max_daily_loss_pct "
                    "(fail-closed)"
                )
            if daily_pnl_pct <= -daily_loss_limit:
                return self._reject(
                    "max_daily_loss_pct exceeded: "
                    f"daily_pnl_pct={daily_pnl_pct} limit=-{daily_loss_limit}"
                )

        max_open = self._settings.max_open_positions
        if (
            opens_new_exposure
            and max_open >= 0
            and open_positions >= max_open
        ):
            return self._reject(
                "max_open_positions reached: "
                f"open_positions={open_positions} limit={max_open}"
            )

        position_size = (portfolio_value * max_position_pct).quantize(_MONEY)
        if position_size <= 0:
            return self._reject("Computed position_size must be positive")

        stop_loss = (entry_price * (Decimal("1") - self._stop_loss_pct)).quantize(_PRICE)
        risk_distance = entry_price - stop_loss
        take_profit = (entry_price + risk_distance * self._risk_reward_ratio).quantize(
            _PRICE
        )

        return RiskEvaluation(
            approved=True,
            reason=f"Approved for {symbol.strip()}: size within max_position_size_pct",
            position_size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    @staticmethod
    def _reject(reason: str) -> RiskEvaluation:
        return RiskEvaluation(
            approved=False,
            reason=reason,
            position_size=Decimal("0"),
            stop_loss=None,
            take_profit=None,
        )
