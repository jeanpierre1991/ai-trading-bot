"""Tests for M7.1 execution_to_fill mapping."""

from __future__ import annotations

from decimal import Decimal

import pytest

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.types import OrderId, Side, Symbol
from portfolio_manager.portfolio import Fill
from runtime.fills import execution_to_fill, is_bookable


def _execution(**overrides: object) -> ExecutionResult:
    values: dict[str, object] = {
        "order_id": OrderId("ord-1"),
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "requested_quantity": Decimal("10"),
        "filled_quantity": Decimal("10"),
        "fill_price": Decimal("190.25"),
        "fee": Decimal("1.50"),
        "status": ExecutionStatus.FILLED,
        "message": "filled",
    }
    values.update(overrides)
    return ExecutionResult(**values)  # type: ignore[arg-type]


def test_filled_buy_maps_to_fill() -> None:
    execution = _execution(
        side=Side.BUY,
        filled_quantity=Decimal("3"),
        fill_price=Decimal("100.50"),
        fee=Decimal("0.25"),
    )

    fill = execution_to_fill(execution)

    assert isinstance(fill, Fill)
    assert fill.symbol == Symbol("AAPL")
    assert fill.side is Side.BUY
    assert fill.quantity == Decimal("3")
    assert fill.price == Decimal("100.50")
    assert fill.fee == Decimal("0.25")


def test_filled_sell_maps_to_fill() -> None:
    execution = _execution(
        side=Side.SELL,
        filled_quantity=Decimal("2"),
        fill_price=Decimal("191.00"),
        fee=Decimal("0"),
    )

    fill = execution_to_fill(execution)

    assert fill is not None
    assert fill.side is Side.SELL
    assert fill.quantity == Decimal("2")
    assert fill.price == Decimal("191.00")
    assert fill.fee == Decimal("0")


def test_filled_uses_filled_quantity_not_requested() -> None:
    execution = _execution(
        requested_quantity=Decimal("10"),
        filled_quantity=Decimal("4"),
        status=ExecutionStatus.FILLED,
    )

    fill = execution_to_fill(execution)

    assert fill is not None
    assert fill.quantity == Decimal("4")


def test_rejected_returns_none() -> None:
    execution = _execution(
        status=ExecutionStatus.REJECTED,
        filled_quantity=Decimal("0"),
        fill_price=Decimal("0"),
        fee=Decimal("0"),
        message="rejected",
    )

    assert is_bookable(execution) is False
    assert execution_to_fill(execution) is None


def test_filled_with_zero_quantity_raises() -> None:
    execution = _execution(filled_quantity=Decimal("0"))

    with pytest.raises(ValueError, match="filled_quantity > 0"):
        execution_to_fill(execution)


def test_filled_with_negative_price_raises() -> None:
    execution = _execution(fill_price=Decimal("-1"))

    with pytest.raises(ValueError, match="negative fill_price"):
        execution_to_fill(execution)


def test_filled_with_negative_fee_raises() -> None:
    execution = _execution(fee=Decimal("-0.01"))

    with pytest.raises(ValueError, match="negative fee"):
        execution_to_fill(execution)


def test_filled_with_empty_symbol_raises() -> None:
    execution = _execution(symbol=Symbol("  "))

    with pytest.raises(ValueError, match="non-empty symbol"):
        execution_to_fill(execution)


def test_filled_with_invalid_side_raises() -> None:
    execution = _execution(side="long")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="invalid side"):
        execution_to_fill(execution)


def test_is_bookable_only_for_filled() -> None:
    assert is_bookable(_execution(status=ExecutionStatus.FILLED)) is True
    assert is_bookable(_execution(status=ExecutionStatus.REJECTED)) is False


def test_mapper_does_not_expose_order_metadata_on_fill() -> None:
    fill = execution_to_fill(_execution(order_id=OrderId("secret-order")))

    assert fill is not None
    assert not hasattr(fill, "order_id")
    assert not hasattr(fill, "status")
    assert not hasattr(fill, "message")
