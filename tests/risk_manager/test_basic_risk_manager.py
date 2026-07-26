"""Tests for BasicRiskManager evaluation logic."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import Settings
from risk_manager.basic import (
    DEFAULT_RISK_REWARD_RATIO,
    DEFAULT_STOP_LOSS_PCT,
    BasicRiskManager,
)


@pytest.fixture
def manager() -> BasicRiskManager:
    return BasicRiskManager(Settings(max_position_size_pct=Decimal("0.05")))


def test_rejects_non_positive_portfolio_value(manager: BasicRiskManager) -> None:
    result = manager.evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is False
    assert "Portfolio value" in result.reason
    assert result.position_size == Decimal("0")
    assert result.stop_loss is None
    assert result.take_profit is None


def test_rejects_non_positive_entry_price(manager: BasicRiskManager) -> None:
    result = manager.evaluate(
        symbol="AAPL",
        entry_price=Decimal("-1"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is False
    assert "Entry price" in result.reason
    assert result.position_size == Decimal("0")
    assert result.stop_loss is None
    assert result.take_profit is None


def test_approves_valid_inputs_and_sizes_from_settings(manager: BasicRiskManager) -> None:
    result = manager.evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.position_size == Decimal("5000.00")
    assert "AAPL" in result.reason


def test_stop_loss_and_take_profit_use_documented_defaults(
    manager: BasicRiskManager,
) -> None:
    entry = Decimal("100")
    result = manager.evaluate(
        symbol="AAPL",
        entry_price=entry,
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    expected_stop = (entry * (Decimal("1") - DEFAULT_STOP_LOSS_PCT)).quantize(
        Decimal("0.0001")
    )
    risk_distance = entry - expected_stop
    expected_tp = (entry + risk_distance * DEFAULT_RISK_REWARD_RATIO).quantize(
        Decimal("0.0001")
    )

    assert result.stop_loss == expected_stop
    assert result.take_profit == expected_tp
    assert result.stop_loss == Decimal("98.0000")
    assert result.take_profit == Decimal("104.0000")


def test_custom_stop_loss_and_risk_reward_overrides() -> None:
    manager = BasicRiskManager(
        Settings(max_position_size_pct=Decimal("0.10")),
        stop_loss_pct=Decimal("0.05"),
        risk_reward_ratio=Decimal("3"),
    )
    result = manager.evaluate(
        symbol="MSFT",
        entry_price=Decimal("200"),
        portfolio_value=Decimal("50000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.position_size == Decimal("5000.00")
    assert result.stop_loss == Decimal("190.0000")
    assert result.take_profit == Decimal("230.0000")


def test_rejects_invalid_constructor_params() -> None:
    with pytest.raises(ValueError, match="stop_loss_pct"):
        BasicRiskManager(Settings(), stop_loss_pct=Decimal("0"))

    with pytest.raises(ValueError, match="risk_reward_ratio"):
        BasicRiskManager(Settings(), risk_reward_ratio=Decimal("0"))
