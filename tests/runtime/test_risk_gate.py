"""Tests for risk gate integration after StrategySignal."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.types import SignalAction
from risk_manager.basic import BasicRiskManager
from risk_manager.models import RiskEvaluation
from runtime.risk_gate import RiskGateResult, apply_risk_gate
from strategy_engine.signal import StrategySignal


def _signal(
    *,
    action: SignalAction = SignalAction.BUY,
    price: Decimal = Decimal("100"),
    symbol: str = "AAPL",
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=action,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=price,
    )


def _manager() -> BasicRiskManager:
    return BasicRiskManager(Settings(max_position_size_pct=Decimal("0.05")))


def test_approved_signal_continues_with_risk_evaluation() -> None:
    signal = _signal(action=SignalAction.BUY, price=Decimal("100"))
    result = apply_risk_gate(
        signal,
        portfolio_value=Decimal("100000"),
        risk_manager=_manager(),
        daily_pnl_pct=Decimal("0"),
    )

    assert isinstance(result, RiskGateResult)
    assert isinstance(result.evaluation, RiskEvaluation)
    assert result.approved is True
    assert result.aborted_reason is None
    assert result.signal is signal
    assert result.signal.action is SignalAction.BUY
    assert result.evaluation.approved is True
    assert result.evaluation.position_size == Decimal("5000.00")
    assert result.evaluation.stop_loss is not None
    assert result.evaluation.take_profit is not None


def test_rejected_signal_stops_cleanly_and_preserves_reason() -> None:
    signal = _signal(action=SignalAction.BUY, price=Decimal("100"))
    result = apply_risk_gate(
        signal,
        portfolio_value=Decimal("0"),
        risk_manager=_manager(),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is False
    assert result.aborted_reason == result.evaluation.reason
    assert result.aborted_reason == "Portfolio value must be positive"
    assert result.signal is signal
    assert result.evaluation.approved is False
    assert result.evaluation.position_size == Decimal("0")


def test_hold_signal_stops_before_risk_sizing() -> None:
    signal = _signal(action=SignalAction.HOLD, price=Decimal("0"))
    result = apply_risk_gate(
        signal,
        portfolio_value=Decimal("100000"),
        risk_manager=_manager(),
    )

    assert result.approved is False
    assert result.aborted_reason == "HOLD signal does not proceed to execution"
    assert result.evaluation.reason == result.aborted_reason
    assert result.signal.action is SignalAction.HOLD


def test_pipeline_unchanged_for_valid_risk_on_sell() -> None:
    signal = _signal(action=SignalAction.SELL, price=Decimal("190.25"), symbol="MSFT")
    result = apply_risk_gate(
        signal,
        portfolio_value=Decimal("50000"),
        risk_manager=_manager(),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.signal.symbol == "MSFT"
    assert result.signal.action is SignalAction.SELL
    assert result.signal.price == Decimal("190.25")
    assert result.signal.strategy_name == "ema_crossover"
    assert result.signal.confidence == 0.8
    assert result.evaluation.position_size == Decimal("2500.00")
