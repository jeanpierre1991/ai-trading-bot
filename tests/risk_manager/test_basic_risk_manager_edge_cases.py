"""Advanced edge-case tests for BasicRiskManager (Milestone 4 B3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import Settings
from risk_manager.basic import (
    DEFAULT_RISK_REWARD_RATIO,
    DEFAULT_STOP_LOSS_PCT,
    BasicRiskManager,
)
from risk_manager.models import RiskEvaluation


def _manager(max_position_size_pct: Decimal = Decimal("0.05")) -> BasicRiskManager:
    return BasicRiskManager(Settings(max_position_size_pct=max_position_size_pct))


@pytest.mark.parametrize(
    ("portfolio_value", "reason_fragment"),
    [
        (Decimal("0"), "Portfolio value"),
        (Decimal("-1000"), "Portfolio value"),
    ],
)
def test_rejects_zero_and_negative_portfolio_value(
    portfolio_value: Decimal,
    reason_fragment: str,
) -> None:
    result = _manager().evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=portfolio_value,
        daily_pnl_pct=Decimal("0"),
    )

    assert isinstance(result, RiskEvaluation)
    assert result.approved is False
    assert reason_fragment in result.reason
    assert result.position_size == Decimal("0")
    assert result.stop_loss is None
    assert result.take_profit is None


@pytest.mark.parametrize(
    ("entry_price", "reason_fragment"),
    [
        (Decimal("0"), "Entry price"),
        (Decimal("-50"), "Entry price"),
    ],
)
def test_rejects_zero_and_negative_entry_price(
    entry_price: Decimal,
    reason_fragment: str,
) -> None:
    result = _manager().evaluate(
        symbol="AAPL",
        entry_price=entry_price,
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert isinstance(result, RiskEvaluation)
    assert result.approved is False
    assert reason_fragment in result.reason
    assert result.position_size == Decimal("0")


@pytest.mark.parametrize(
    ("max_position_size_pct", "reason_fragment"),
    [
        (Decimal("0"), "max_position_size_pct must be positive"),
        (Decimal("-0.05"), "max_position_size_pct must be positive"),
        (Decimal("1.5"), "max_position_size_pct cannot exceed 1"),
    ],
)
def test_rejects_invalid_max_position_size_pct(
    max_position_size_pct: Decimal,
    reason_fragment: str,
) -> None:
    result = _manager(max_position_size_pct).evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert isinstance(result, RiskEvaluation)
    assert result.approved is False
    assert reason_fragment in result.reason
    assert result.position_size == Decimal("0")
    assert result.stop_loss is None
    assert result.take_profit is None


def test_decimal_precision_is_preserved_for_odd_sizes() -> None:
    result = _manager(Decimal("0.0333")).evaluate(
        symbol="AAPL",
        entry_price=Decimal("123.4567"),
        portfolio_value=Decimal("10000.11"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.position_size == Decimal("333.00")
    assert isinstance(result.position_size, Decimal)
    assert isinstance(result.stop_loss, Decimal)
    assert isinstance(result.take_profit, Decimal)


def test_stop_loss_below_and_take_profit_above_entry() -> None:
    entry = Decimal("250.50")
    result = _manager().evaluate(
        symbol="AAPL",
        entry_price=entry,
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.approved is True
    assert result.stop_loss is not None
    assert result.take_profit is not None
    assert result.stop_loss < entry
    assert result.take_profit > entry


def test_take_profit_respects_risk_reward_distance() -> None:
    entry = Decimal("100")
    result = _manager().evaluate(
        symbol="AAPL",
        entry_price=entry,
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert result.stop_loss is not None
    assert result.take_profit is not None
    risk_distance = entry - result.stop_loss
    reward_distance = result.take_profit - entry
    assert reward_distance == (risk_distance * DEFAULT_RISK_REWARD_RATIO).quantize(
        Decimal("0.0001")
    )
    assert result.stop_loss == (
        entry * (Decimal("1") - DEFAULT_STOP_LOSS_PCT)
    ).quantize(Decimal("0.0001"))


@pytest.mark.parametrize("symbol", ["", "   ", "\t"])
def test_rejects_empty_or_whitespace_symbol(symbol: str) -> None:
    result = _manager().evaluate(
        symbol=symbol,
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert isinstance(result, RiskEvaluation)
    assert result.approved is False
    assert "Symbol" in result.reason
    assert result.position_size == Decimal("0")


def test_evaluate_always_returns_risk_evaluation() -> None:
    cases = [
        ("AAPL", Decimal("100"), Decimal("100000")),
        ("", Decimal("100"), Decimal("100000")),
        ("AAPL", Decimal("0"), Decimal("100000")),
        ("AAPL", Decimal("100"), Decimal("-1")),
    ]
    for symbol, entry_price, portfolio_value in cases:
        result = _manager().evaluate(
            symbol=symbol,
            entry_price=entry_price,
            portfolio_value=portfolio_value,
            daily_pnl_pct=Decimal("0"),
        )
        assert isinstance(result, RiskEvaluation)
        assert isinstance(result.reason, str)
        assert result.reason


def test_rejection_reasons_are_explicit() -> None:
    portfolio_reject = _manager().evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
    )
    price_reject = _manager().evaluate(
        symbol="AAPL",
        entry_price=Decimal("0"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )
    pct_reject = _manager(Decimal("0")).evaluate(
        symbol="AAPL",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )
    symbol_reject = _manager().evaluate(
        symbol=" ",
        entry_price=Decimal("100"),
        portfolio_value=Decimal("100000"),
        daily_pnl_pct=Decimal("0"),
    )

    assert "Portfolio value must be positive" == portfolio_reject.reason
    assert "Entry price must be positive" == price_reject.reason
    assert "max_position_size_pct must be positive" == pct_reject.reason
    assert "Symbol must be a non-empty string" == symbol_reject.reason


def test_evaluate_is_deterministic() -> None:
    manager = _manager()
    kwargs = {
        "symbol": "AAPL",
        "entry_price": Decimal("100.25"),
        "portfolio_value": Decimal("75000.50"),
        "daily_pnl_pct": Decimal("0"),
    }

    first = manager.evaluate(**kwargs)
    second = manager.evaluate(**kwargs)

    assert first == second
    assert first.approved is True
    assert first.position_size == second.position_size
    assert first.stop_loss == second.stop_loss
    assert first.take_profit == second.take_profit
