"""Risk gate between strategy signals and downstream execution stages.

This is the minimal integration point for Milestone 4: a StrategySignal is
evaluated by BasicRiskManager before it may continue in the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.types import SignalAction
from risk_manager.base import RiskManager
from risk_manager.models import RiskEvaluation
from strategy_engine.signal import StrategySignal


@dataclass(frozen=True)
class RiskGateResult:
    """Outcome of applying risk controls to a strategy signal."""

    approved: bool
    signal: StrategySignal
    evaluation: RiskEvaluation
    aborted_reason: str | None = None


def apply_risk_gate(
    signal: StrategySignal,
    *,
    portfolio_value: Decimal,
    risk_manager: RiskManager,
) -> RiskGateResult:
    """Evaluate ``signal`` with ``risk_manager`` and gate continuation.

    HOLD signals stop before risk sizing. Actionable signals are passed to
    ``RiskManager.evaluate``; rejection preserves ``RiskEvaluation.reason``.
    """
    if signal.action is SignalAction.HOLD:
        evaluation = RiskEvaluation(
            approved=False,
            reason="HOLD signal does not proceed to execution",
            position_size=Decimal("0"),
            stop_loss=None,
            take_profit=None,
        )
        return RiskGateResult(
            approved=False,
            signal=signal,
            evaluation=evaluation,
            aborted_reason=evaluation.reason,
        )

    evaluation = risk_manager.evaluate(
        symbol=signal.symbol,
        entry_price=signal.price,
        portfolio_value=portfolio_value,
    )

    if not evaluation.approved:
        return RiskGateResult(
            approved=False,
            signal=signal,
            evaluation=evaluation,
            aborted_reason=evaluation.reason,
        )

    return RiskGateResult(
        approved=True,
        signal=signal,
        evaluation=evaluation,
        aborted_reason=None,
    )
