"""Tests for DryRunExecutor."""

from __future__ import annotations

from decimal import Decimal

from broker_interface.execution import ExecutionStatus
from core.types import OrderType, Side, Symbol
from runtime.dry_run import DRY_RUN_EXECUTED_AT, DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent


def _intent(**overrides: object) -> TradeIntent:
    values: dict[str, object] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("10"),
        "limit_price": None,
        "strategy_name": "ema_crossover",
        "signal_confidence": 0.8,
        "max_position_value": Decimal("1000.00"),
        "reason": "approved",
    }
    values.update(overrides)
    return TradeIntent(**values)  # type: ignore[arg-type]


def test_dry_run_executor_is_order_executor() -> None:
    assert isinstance(DryRunExecutor(), OrderExecutor)


def test_valid_intent_returns_filled_dry_run_result() -> None:
    executor = DryRunExecutor()
    intent = _intent()

    result = executor.execute(intent)

    assert result.status is ExecutionStatus.FILLED
    assert result.symbol == Symbol("AAPL")
    assert result.side is Side.BUY
    assert result.requested_quantity == Decimal("10")
    assert result.filled_quantity == Decimal("10")
    assert result.fill_price == Decimal("100.0000")
    assert result.fee == Decimal("0")
    assert result.executed_at == DRY_RUN_EXECUTED_AT
    assert "Dry-run" in result.message
    assert "no broker" in result.message.lower()
    assert str(result.order_id).startswith("dry-run-")


def test_missing_intent_is_rejected() -> None:
    result = DryRunExecutor().execute(None)

    assert result.status is ExecutionStatus.REJECTED
    assert result.filled_quantity == Decimal("0")
    assert result.message == "TradeIntent is required for dry-run execution"


def test_non_positive_quantity_is_rejected() -> None:
    result = DryRunExecutor().execute(_intent(quantity=Decimal("0")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Quantity must be positive"
    assert result.filled_quantity == Decimal("0")


def test_empty_symbol_is_rejected() -> None:
    result = DryRunExecutor().execute(_intent(symbol=Symbol("  ")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Symbol must be a non-empty string"


def test_unsupported_order_type_is_rejected() -> None:
    result = DryRunExecutor().execute(_intent(order_type=OrderType.LIMIT))

    assert result.status is ExecutionStatus.REJECTED
    assert "Unsupported order_type" in result.message


def test_repeated_intent_is_deterministic() -> None:
    executor = DryRunExecutor()
    intent = _intent(quantity=Decimal("3.5"), max_position_value=Decimal("700"))

    first = executor.execute(intent)
    second = executor.execute(intent)

    assert first == second
