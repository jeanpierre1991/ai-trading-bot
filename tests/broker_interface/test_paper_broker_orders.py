"""Tests for BrokerOrderRequest and PaperBroker.place_order."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from broker_interface.broker import Broker, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from core.types import OrderType, Side, Symbol


def _request(**overrides: object) -> BrokerOrderRequest:
    values: dict[str, object] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("1"),
        "limit_price": None,
    }
    values.update(overrides)
    return BrokerOrderRequest(**values)  # type: ignore[arg-type]


def test_broker_order_request_construction() -> None:
    request = BrokerOrderRequest(
        symbol=Symbol("MSFT"),
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2.5"),
        limit_price=Decimal("420.00"),
    )

    assert request.symbol == Symbol("MSFT")
    assert request.side is Side.SELL
    assert request.order_type is OrderType.LIMIT
    assert request.quantity == Decimal("2.5")
    assert request.limit_price == Decimal("420.00")


def test_broker_order_request_limit_price_defaults_to_none() -> None:
    request = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )

    assert request.limit_price is None


def test_broker_order_request_is_immutable() -> None:
    request = _request()

    with pytest.raises(FrozenInstanceError):
        request.quantity = Decimal("2")  # type: ignore[misc]


def test_broker_order_request_equality() -> None:
    left = _request(quantity=Decimal("3"))
    right = _request(quantity=Decimal("3"))
    different = _request(quantity=Decimal("4"))

    assert left == right
    assert left != different


def test_paper_broker_is_broker_subclass() -> None:
    assert issubclass(PaperBroker, Broker)


def test_place_order_requires_connection() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))

    result = broker.place_order(_request())

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Broker is disconnected"
    assert result.filled_quantity == Decimal("0")
    assert result.fill_price == Decimal("0")


def test_place_order_market_buy_fills_at_quote_and_debits_buying_power() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))
    broker.connect()
    quote = broker.get_quote(Symbol("AAPL"))

    result = broker.place_order(_request(quantity=Decimal("2")))

    assert isinstance(result, ExecutionResult)
    assert result.status is ExecutionStatus.FILLED
    assert result.symbol == Symbol("AAPL")
    assert result.side is Side.BUY
    assert result.requested_quantity == Decimal("2")
    assert result.filled_quantity == Decimal("2")
    assert result.fill_price == quote
    assert result.fee == Decimal("0")
    assert result.message == "Paper order filled"
    assert broker.get_status().buying_power == Decimal("100000") - (Decimal("2") * quote).quantize(
        Decimal("0.01")
    )


def test_place_order_market_sell_fills_and_credits_buying_power() -> None:
    broker = PaperBroker(buying_power=Decimal("1000"))
    broker.connect()
    quote = broker.get_quote(Symbol("AAPL"))

    result = broker.place_order(
        _request(side=Side.SELL, quantity=Decimal("1")),
    )

    assert result.status is ExecutionStatus.FILLED
    assert result.side is Side.SELL
    assert result.fill_price == quote
    assert broker.get_status().buying_power == Decimal("1000") + quote.quantize(Decimal("0.01"))


def test_place_order_rejects_non_positive_quantity() -> None:
    broker = PaperBroker()
    broker.connect()

    result = broker.place_order(_request(quantity=Decimal("0")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Quantity must be positive"
    assert broker.get_status().buying_power == Decimal("100000")


def test_place_order_rejects_empty_symbol() -> None:
    broker = PaperBroker()
    broker.connect()

    result = broker.place_order(_request(symbol=Symbol("  ")))

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Symbol must be a non-empty string"


def test_place_order_rejects_invalid_side() -> None:
    broker = PaperBroker()
    broker.connect()
    bad = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side="long",  # type: ignore[arg-type]
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )

    result = broker.place_order(bad)

    assert result.status is ExecutionStatus.REJECTED
    assert "Invalid side" in result.message
    assert broker.get_status().buying_power == Decimal("100000")


def test_place_order_rejects_non_market_order_type() -> None:
    broker = PaperBroker()
    broker.connect()

    result = broker.place_order(
        _request(order_type=OrderType.LIMIT, limit_price=Decimal("100")),
    )

    assert result.status is ExecutionStatus.REJECTED
    assert "Unsupported order_type" in result.message
    assert "MARKET" in result.message


def test_place_order_rejects_insufficient_buying_power() -> None:
    broker = PaperBroker(buying_power=Decimal("100"))
    broker.connect()
    # AAPL quote 190.25 > 100
    result = broker.place_order(_request(quantity=Decimal("1")))

    assert result.status is ExecutionStatus.REJECTED
    assert "Insufficient buying power" in result.message
    assert result.filled_quantity == Decimal("0")
    assert broker.get_status().buying_power == Decimal("100")


def test_place_order_does_not_mutate_on_rejection() -> None:
    broker = PaperBroker(buying_power=Decimal("50"))
    broker.connect()
    before = broker.get_status().buying_power

    rejected = broker.place_order(_request(quantity=Decimal("10")))

    assert rejected.status is ExecutionStatus.REJECTED
    assert broker.get_status().buying_power == before


def test_place_order_unknown_symbol_uses_default_quote() -> None:
    broker = PaperBroker(buying_power=Decimal("100000"))
    broker.connect()

    result = broker.place_order(_request(symbol=Symbol("NFLX"), quantity=Decimal("1")))

    assert result.status is ExecutionStatus.FILLED
    assert result.fill_price == Decimal("100.00")
    assert broker.get_status().buying_power == Decimal("99900.00")


def test_place_order_after_disconnect_is_rejected() -> None:
    broker = PaperBroker()
    broker.connect()
    broker.disconnect()

    result = broker.place_order(_request())

    assert result.status is ExecutionStatus.REJECTED
    assert result.message == "Broker is disconnected"
