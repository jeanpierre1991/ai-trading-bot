"""Tests for portfolio domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from core.types import PositionId, Side, Symbol
from portfolio_manager.portfolio import Fill, Portfolio, Position


def test_fill_construction_and_defaults() -> None:
    fill = Fill(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("190.25"),
    )

    assert fill.symbol == Symbol("AAPL")
    assert fill.side is Side.BUY
    assert fill.quantity == Decimal("10")
    assert fill.price == Decimal("190.25")
    assert fill.fee == Decimal("0")


def test_fill_is_immutable() -> None:
    fill = Fill(
        symbol=Symbol("MSFT"),
        side=Side.SELL,
        quantity=Decimal("5"),
        price=Decimal("420.50"),
        fee=Decimal("1.25"),
    )

    with pytest.raises(FrozenInstanceError):
        fill.quantity = Decimal("6")  # type: ignore[misc]


def test_fill_equality() -> None:
    left = Fill(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
    )
    right = Fill(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
    )
    different = Fill(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0"),
    )

    assert left == right
    assert left != different
    assert hash(left) == hash(right)


def test_position_construction_and_metrics() -> None:
    position = Position(
        position_id=PositionId("pos-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("100.00"),
        current_price=Decimal("110.00"),
    )

    assert position.market_value == Decimal("1100.00")
    assert position.unrealized_pnl == Decimal("100.00")
    assert position.to_dict()["symbol"] == "AAPL"
    assert position.to_dict()["side"] == "buy"


def test_portfolio_construction_and_summary() -> None:
    portfolio = Portfolio(cash=Decimal("100000.00"))
    position = Position(
        position_id=PositionId("pos-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("100.00"),
        current_price=Decimal("100.00"),
    )
    portfolio.positions["AAPL"] = position

    assert portfolio.position_count == 1
    assert portfolio.positions_value == Decimal("1000.00")
    assert portfolio.total_value == Decimal("101000.00")
    assert portfolio.summary() == {
        "cash": 100000.0,
        "positions_value": 1000.0,
        "total_value": 101000.0,
        "position_count": 1,
    }


def test_empty_portfolio_summary() -> None:
    portfolio = Portfolio(cash=Decimal("50000"))

    assert portfolio.position_count == 0
    assert portfolio.positions_value == Decimal("0")
    assert portfolio.total_value == Decimal("50000.00")
    assert portfolio.summary()["position_count"] == 0
