"""Tests for Milestone 4 RiskManager base structure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from risk_manager.base import RiskManager
from risk_manager.models import RiskEvaluation


def test_risk_evaluation_construction() -> None:
    evaluation = RiskEvaluation(
        approved=True,
        reason="Within limits",
        position_size=Decimal("1000.00"),
        stop_loss=Decimal("95.00"),
        take_profit=Decimal("110.00"),
    )

    assert evaluation.approved is True
    assert evaluation.reason == "Within limits"
    assert evaluation.position_size == Decimal("1000.00")
    assert evaluation.stop_loss == Decimal("95.00")
    assert evaluation.take_profit == Decimal("110.00")


def test_risk_evaluation_allows_optional_levels() -> None:
    evaluation = RiskEvaluation(
        approved=False,
        reason="Rejected",
        position_size=Decimal("0"),
        stop_loss=None,
        take_profit=None,
    )

    assert evaluation.approved is False
    assert evaluation.stop_loss is None
    assert evaluation.take_profit is None


def test_risk_evaluation_is_immutable() -> None:
    evaluation = RiskEvaluation(
        approved=True,
        reason="ok",
        position_size=Decimal("1"),
        stop_loss=None,
        take_profit=None,
    )

    with pytest.raises(FrozenInstanceError):
        evaluation.approved = False  # type: ignore[misc]


def test_risk_evaluation_equality() -> None:
    left = RiskEvaluation(
        approved=True,
        reason="ok",
        position_size=Decimal("10"),
        stop_loss=Decimal("9"),
        take_profit=Decimal("12"),
    )
    right = RiskEvaluation(
        approved=True,
        reason="ok",
        position_size=Decimal("10"),
        stop_loss=Decimal("9"),
        take_profit=Decimal("12"),
    )
    different = RiskEvaluation(
        approved=False,
        reason="ok",
        position_size=Decimal("10"),
        stop_loss=Decimal("9"),
        take_profit=Decimal("12"),
    )

    assert left == right
    assert left != different
    assert hash(left) == hash(right)


def test_risk_manager_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        RiskManager()  # type: ignore[misc]


def test_risk_manager_concrete_subclass_can_implement_evaluate() -> None:
    class StubRiskManager(RiskManager):
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
            return RiskEvaluation(
                approved=True,
                reason=f"stub:{symbol}",
                position_size=(portfolio_value * Decimal("0.01")).quantize(Decimal("0.01")),
                stop_loss=entry_price * Decimal("0.95"),
                take_profit=entry_price * Decimal("1.05"),
            )

    result = StubRiskManager().evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.reason == "stub:AAPL"
    assert result.position_size == Decimal("1000.00")
    assert result.stop_loss == Decimal("95.00")
    assert result.take_profit == Decimal("105.00")
