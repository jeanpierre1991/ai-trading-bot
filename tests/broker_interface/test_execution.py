"""Tests for broker execution contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from broker_interface.execution import ExecutionResult, ExecutionStatus
from core.types import OrderId, Side, Symbol


def test_execution_status_values() -> None:
    assert ExecutionStatus.FILLED.value == "filled"
    assert ExecutionStatus.REJECTED.value == "rejected"
    assert set(ExecutionStatus) == {ExecutionStatus.FILLED, ExecutionStatus.REJECTED}


def test_build_filled_execution_result() -> None:
    executed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    result = ExecutionResult(
        order_id=OrderId("ord-filled-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        requested_quantity=Decimal("10"),
        filled_quantity=Decimal("10"),
        fill_price=Decimal("190.25"),
        fee=Decimal("0"),
        status=ExecutionStatus.FILLED,
        message="Order filled",
        executed_at=executed_at,
    )

    assert result.order_id == OrderId("ord-filled-1")
    assert result.symbol == Symbol("AAPL")
    assert result.side is Side.BUY
    assert result.requested_quantity == Decimal("10")
    assert result.filled_quantity == Decimal("10")
    assert result.fill_price == Decimal("190.25")
    assert result.fee == Decimal("0")
    assert result.status is ExecutionStatus.FILLED
    assert result.message == "Order filled"
    assert result.executed_at == executed_at


def test_build_rejected_execution_result() -> None:
    result = ExecutionResult(
        order_id=OrderId("ord-rejected-1"),
        symbol=Symbol("MSFT"),
        side=Side.SELL,
        requested_quantity=Decimal("5"),
        filled_quantity=Decimal("0"),
        fill_price=Decimal("0"),
        fee=Decimal("0"),
        status=ExecutionStatus.REJECTED,
        message="Insufficient buying power",
    )

    assert result.status is ExecutionStatus.REJECTED
    assert result.filled_quantity == Decimal("0")
    assert result.fill_price == Decimal("0")
    assert result.message == "Insufficient buying power"
    assert isinstance(result.executed_at, datetime)
    assert result.executed_at.tzinfo is not None


def test_execution_result_is_immutable() -> None:
    result = ExecutionResult(
        order_id=OrderId("ord-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        fee=Decimal("0"),
        status=ExecutionStatus.FILLED,
        message="filled",
    )

    with pytest.raises(FrozenInstanceError):
        result.message = "changed"  # type: ignore[misc]
