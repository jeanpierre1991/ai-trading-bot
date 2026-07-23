"""Tests for BrokerOrderExecutor and module place_order delegation."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from broker_interface.broker import PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.module import BrokerInterfaceModule
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.types import OrderId, OrderType, Side, Symbol
from runtime.broker_executor import BrokerOrderExecutor
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import TradeIntent


def _intent(**overrides: object) -> TradeIntent:
    values: dict[str, object] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("2"),
        "limit_price": None,
        "strategy_name": "ema_crossover",
        "signal_confidence": 0.8,
        "max_position_value": Decimal("5000.00"),
        "reason": "approved",
    }
    values.update(overrides)
    return TradeIntent(**values)  # type: ignore[arg-type]


def test_broker_order_executor_is_order_executor() -> None:
    assert isinstance(BrokerOrderExecutor(PaperBroker()), OrderExecutor)


def test_broker_order_executor_stores_injected_broker() -> None:
    broker = PaperBroker()
    executor = BrokerOrderExecutor(broker)

    assert executor.broker is broker


def test_execute_maps_intent_and_delegates_to_broker() -> None:
    broker = MagicMock()
    expected = ExecutionResult(
        order_id=OrderId("ord-1"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        requested_quantity=Decimal("2"),
        filled_quantity=Decimal("2"),
        fill_price=Decimal("190.25"),
        fee=Decimal("0"),
        status=ExecutionStatus.FILLED,
        message="Paper order filled",
    )
    broker.place_order.return_value = expected
    executor = BrokerOrderExecutor(broker)
    intent = _intent(quantity=Decimal("2"), limit_price=None)

    result = executor.execute(intent)

    assert result is expected
    broker.place_order.assert_called_once()
    request = broker.place_order.call_args.args[0]
    assert isinstance(request, BrokerOrderRequest)
    assert request == BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("2"),
        limit_price=None,
    )


def test_execute_does_not_forward_runtime_metadata_to_broker() -> None:
    broker = MagicMock()
    broker.place_order.return_value = ExecutionResult(
        order_id=OrderId("ord-2"),
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        fee=Decimal("0"),
        status=ExecutionStatus.FILLED,
        message="ok",
    )
    executor = BrokerOrderExecutor(broker)

    executor.execute(
        _intent(
            strategy_name="secret-strategy",
            signal_confidence=0.99,
            max_position_value=Decimal("99999"),
            reason="do-not-leak",
        )
    )

    request = broker.place_order.call_args.args[0]
    assert not hasattr(request, "strategy_name")
    assert not hasattr(request, "signal_confidence")
    assert not hasattr(request, "max_position_value")
    assert not hasattr(request, "reason")


def test_missing_intent_rejects_without_calling_broker() -> None:
    broker = MagicMock()
    executor = BrokerOrderExecutor(broker)

    result = executor.execute(None)

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "TradeIntent is required for broker execution"
    assert result.filled_quantity == Decimal("0")
    broker.place_order.assert_not_called()


def test_empty_symbol_rejects_without_calling_broker() -> None:
    broker = MagicMock()
    executor = BrokerOrderExecutor(broker)

    result = executor.execute(_intent(symbol=Symbol("  ")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Symbol must be a non-empty string"
    broker.place_order.assert_not_called()


def test_non_positive_quantity_rejects_without_calling_broker() -> None:
    broker = MagicMock()
    executor = BrokerOrderExecutor(broker)

    result = executor.execute(_intent(quantity=Decimal("0")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Quantity must be positive"
    broker.place_order.assert_not_called()


def test_invalid_side_rejects_without_calling_broker() -> None:
    broker = MagicMock()
    executor = BrokerOrderExecutor(broker)
    bad = TradeIntent(
        symbol=Symbol("AAPL"),
        side="long",  # type: ignore[arg-type]
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        limit_price=None,
        strategy_name="ema_crossover",
        signal_confidence=0.5,
        max_position_value=Decimal("100"),
        reason="bad",
    )

    result = executor.execute(bad)

    assert result.status is ExecutionStatus.REJECTED
    assert "Unsupported side" in result.message
    broker.place_order.assert_not_called()


def test_non_market_order_type_rejects_without_calling_broker() -> None:
    broker = MagicMock()
    executor = BrokerOrderExecutor(broker)

    result = executor.execute(
        _intent(order_type=OrderType.LIMIT, limit_price=Decimal("100")),
    )

    assert result.status is ExecutionStatus.REJECTED
    assert "Unsupported order_type" in result.message
    broker.place_order.assert_not_called()


def test_execute_with_paper_broker_fills_market_buy() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))
    broker.connect()
    executor = BrokerOrderExecutor(broker)
    quote = broker.get_quote(Symbol("AAPL"))

    result = executor.execute(_intent(quantity=Decimal("1")))

    assert result.status is ExecutionStatus.FILLED
    assert result.fill_price == quote
    assert result.filled_quantity == Decimal("1")
    assert result.message == "Paper order filled"


def test_broker_rejection_is_returned_as_is() -> None:
    broker = PaperBroker(buying_power=Decimal("1"))
    broker.connect()
    executor = BrokerOrderExecutor(broker)

    result = executor.execute(_intent(quantity=Decimal("1")))

    assert result.status is ExecutionStatus.REJECTED
    assert "Insufficient buying power" in result.message


def test_dry_run_executor_remains_compatible() -> None:
    dry = DryRunExecutor()
    brokered = BrokerOrderExecutor(PaperBroker())

    assert isinstance(dry, OrderExecutor)
    assert isinstance(brokered, OrderExecutor)
    assert dry.execute(_intent()).status is ExecutionStatus.FILLED


def test_module_place_order_delegates_to_broker() -> None:
    settings = Settings(broker_name="paper", backtest_initial_capital=Decimal("50000"))
    module = BrokerInterfaceModule(settings)
    module.initialize()
    request = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )

    result = module.place_order(request)

    assert result.status is ExecutionStatus.FILLED
    assert result.symbol == Symbol("AAPL")
    module.shutdown()
