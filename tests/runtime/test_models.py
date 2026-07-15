"""Tests for runtime domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.types import OrderId, OrderType, Side, SignalAction, Symbol
from order_manager.manager import OrderRecord, OrderState
from risk_manager.rules import RiskAssessment
from runtime.models import PipelineResult, TradeIntent
from strategy_engine.signal import StrategySignal


def test_trade_intent_construction() -> None:
    intent = TradeIntent(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        limit_price=None,
        strategy_name="ema_crossover",
        signal_confidence=0.75,
        max_position_value=Decimal("5000.00"),
        reason="Risk approved",
    )

    assert intent.symbol == Symbol("AAPL")
    assert intent.side is Side.BUY
    assert intent.order_type is OrderType.MARKET
    assert intent.quantity == Decimal("10")
    assert intent.limit_price is None
    assert intent.strategy_name == "ema_crossover"
    assert intent.signal_confidence == 0.75
    assert intent.max_position_value == Decimal("5000.00")
    assert intent.reason == "Risk approved"


def test_trade_intent_is_immutable() -> None:
    intent = TradeIntent(
        symbol=Symbol("AAPL"),
        side=Side.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        limit_price=Decimal("190.00"),
        strategy_name="rsi",
        signal_confidence=0.6,
        max_position_value=Decimal("1000"),
        reason="ok",
    )

    with pytest.raises(FrozenInstanceError):
        intent.quantity = Decimal("2")  # type: ignore[misc]


def test_pipeline_result_aborted_construction() -> None:
    signal = StrategySignal(
        symbol="AAPL",
        action=SignalAction.HOLD,
        confidence=0.5,
        strategy_name="rsi",
        price=Decimal("190.00"),
    )
    result = PipelineResult(
        success=False,
        stage_reached="strategy",
        aborted_reason="HOLD signal",
        signal=signal,
        alerts_sent=1,
    )

    assert result.success is False
    assert result.stage_reached == "strategy"
    assert result.aborted_reason == "HOLD signal"
    assert result.signal is signal
    assert result.risk_assessment is None
    assert result.intent is None
    assert result.order is None
    assert result.execution is None
    assert result.portfolio_snapshot is None
    assert result.alerts_sent == 1


def test_pipeline_result_success_construction() -> None:
    signal = StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        confidence=0.8,
        strategy_name="ema_crossover",
        price=Decimal("190.25"),
    )
    assessment = RiskAssessment(
        approved=True,
        max_position_value=Decimal("5000.00"),
        reason="All risk checks passed",
        checks_passed=["position_size", "open_positions", "daily_loss"],
        checks_failed=[],
    )
    intent = TradeIntent(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        limit_price=None,
        strategy_name="ema_crossover",
        signal_confidence=0.8,
        max_position_value=Decimal("5000.00"),
        reason="All risk checks passed",
    )
    order = OrderRecord(
        order_id=OrderId("ord-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        price=None,
        state=OrderState.FILLED,
    )
    execution = ExecutionResult(
        order_id=OrderId("ord-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        requested_quantity=Decimal("10"),
        filled_quantity=Decimal("10"),
        fill_price=Decimal("190.25"),
        fee=Decimal("0"),
        status=ExecutionStatus.FILLED,
        message="Order filled",
    )
    snapshot = {
        "cash": 98097.5,
        "positions_value": 1902.5,
        "total_value": 100000.0,
        "position_count": 1,
    }

    result = PipelineResult(
        success=True,
        stage_reached="alerts",
        signal=signal,
        risk_assessment=assessment,
        intent=intent,
        order=order,
        execution=execution,
        portfolio_snapshot=snapshot,
        alerts_sent=1,
    )

    assert result.success is True
    assert result.stage_reached == "alerts"
    assert result.aborted_reason is None
    assert result.signal is signal
    assert result.risk_assessment is assessment
    assert result.intent is intent
    assert result.order is order
    assert result.execution is execution
    assert result.portfolio_snapshot == snapshot
    assert result.alerts_sent == 1


def test_pipeline_result_is_immutable() -> None:
    result = PipelineResult(success=False, stage_reached="market_data", aborted_reason="empty bars")

    with pytest.raises(FrozenInstanceError):
        result.success = True  # type: ignore[misc]


def _sample_trade_intent(**overrides: object) -> TradeIntent:
    values: dict[str, object] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("10"),
        "limit_price": None,
        "strategy_name": "ema_crossover",
        "signal_confidence": 0.75,
        "max_position_value": Decimal("5000.00"),
        "reason": "Risk approved",
    }
    values.update(overrides)
    return TradeIntent(**values)  # type: ignore[arg-type]


def test_trade_intent_equality() -> None:
    left = _sample_trade_intent()
    right = _sample_trade_intent()
    different = _sample_trade_intent(quantity=Decimal("11"))

    assert left == right
    assert left != different
    assert hash(left) == hash(right)


def test_trade_intent_repr() -> None:
    intent = _sample_trade_intent()
    representation = repr(intent)

    assert "TradeIntent" in representation
    assert "AAPL" in representation
    assert "ema_crossover" in representation
    assert "10" in representation


def test_pipeline_result_default_values() -> None:
    result = PipelineResult(success=True, stage_reached="alerts")

    assert result.aborted_reason is None
    assert result.signal is None
    assert result.risk_assessment is None
    assert result.intent is None
    assert result.order is None
    assert result.execution is None
    assert result.portfolio_snapshot is None
    assert result.alerts_sent == 0


def test_pipeline_result_equality() -> None:
    left = PipelineResult(
        success=False,
        stage_reached="risk",
        aborted_reason="Failed checks: position_size",
        alerts_sent=1,
    )
    right = PipelineResult(
        success=False,
        stage_reached="risk",
        aborted_reason="Failed checks: position_size",
        alerts_sent=1,
    )
    different = PipelineResult(
        success=False,
        stage_reached="risk",
        aborted_reason="Failed checks: open_positions",
        alerts_sent=1,
    )

    assert left == right
    assert left != different
    assert hash(left) == hash(right)


def test_pipeline_result_repr() -> None:
    result = PipelineResult(
        success=False,
        stage_reached="strategy",
        aborted_reason="HOLD signal",
        alerts_sent=1,
    )
    representation = repr(result)

    assert "PipelineResult" in representation
    assert "strategy" in representation
    assert "HOLD signal" in representation
    assert "alerts_sent=1" in representation
