"""Tests for Portfolio.apply_fill update logic."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.types import Side, Symbol
from portfolio_manager.portfolio import Fill, Portfolio


def test_buy_opens_long_position_and_debits_cash() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("100.00"),
        )
    )

    position = portfolio.positions["AAPL"]
    assert portfolio.cash == Decimal("9000.00")
    assert position.side is Side.BUY
    assert position.quantity == Decimal("10")
    assert position.entry_price == Decimal("100.00")
    assert position.current_price == Decimal("100.00")
    assert portfolio.position_count == 1


def test_buy_increases_existing_long_with_weighted_entry() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("10"), price=Decimal("100"))
    )
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("10"), price=Decimal("120"))
    )

    position = portfolio.positions["AAPL"]
    assert position.quantity == Decimal("20")
    assert position.entry_price == Decimal("110.0000")
    assert position.current_price == Decimal("120")
    assert portfolio.cash == Decimal("7800.00")


def test_buy_debits_fee() -> None:
    portfolio = Portfolio(cash=Decimal("1000.00"))
    portfolio.apply_fill(
        Fill(
            symbol=Symbol("AAPL"),
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("100.00"),
            fee=Decimal("2.50"),
        )
    )

    assert portfolio.cash == Decimal("897.50")


def test_buy_insufficient_cash_raises() -> None:
    portfolio = Portfolio(cash=Decimal("50.00"))

    with pytest.raises(ValueError, match="Insufficient cash"):
        portfolio.apply_fill(
            Fill(
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("100.00"),
            )
        )

    assert portfolio.cash == Decimal("50.00")
    assert portfolio.position_count == 0


def test_sell_reduces_position_and_credits_cash() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("10"), price=Decimal("100"))
    )
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.SELL, quantity=Decimal("4"), price=Decimal("110"))
    )

    position = portfolio.positions["AAPL"]
    assert position.quantity == Decimal("6")
    assert position.current_price == Decimal("110")
    assert portfolio.cash == Decimal("9440.00")


def test_sell_closes_position_when_quantity_matches() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("10"), price=Decimal("100"))
    )
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.SELL, quantity=Decimal("10"), price=Decimal("105"))
    )

    assert "AAPL" not in portfolio.positions
    assert portfolio.position_count == 0
    assert portfolio.cash == Decimal("10050.00")


def test_sell_debits_fee_from_proceeds() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("10"), price=Decimal("100"))
    )
    portfolio.apply_fill(
        Fill(
            symbol=Symbol("AAPL"),
            side=Side.SELL,
            quantity=Decimal("10"),
            price=Decimal("100"),
            fee=Decimal("5.00"),
        )
    )

    assert portfolio.cash == Decimal("9995.00")
    assert portfolio.position_count == 0


def test_sell_without_position_raises() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))

    with pytest.raises(ValueError, match="no open position"):
        portfolio.apply_fill(
            Fill(
                symbol=Symbol("AAPL"),
                side=Side.SELL,
                quantity=Decimal("1"),
                price=Decimal("100"),
            )
        )


def test_oversell_raises_and_leaves_state_unchanged() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))
    portfolio.apply_fill(
        Fill(symbol=Symbol("AAPL"), side=Side.BUY, quantity=Decimal("5"), price=Decimal("100"))
    )
    cash_before = portfolio.cash
    qty_before = portfolio.positions["AAPL"].quantity

    with pytest.raises(ValueError, match="Oversell"):
        portfolio.apply_fill(
            Fill(
                symbol=Symbol("AAPL"),
                side=Side.SELL,
                quantity=Decimal("6"),
                price=Decimal("100"),
            )
        )

    assert portfolio.cash == cash_before
    assert portfolio.positions["AAPL"].quantity == qty_before


def test_invalid_fill_quantity_raises() -> None:
    portfolio = Portfolio(cash=Decimal("10000.00"))

    with pytest.raises(ValueError, match="quantity must be positive"):
        portfolio.apply_fill(
            Fill(
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                quantity=Decimal("0"),
                price=Decimal("100"),
            )
        )
