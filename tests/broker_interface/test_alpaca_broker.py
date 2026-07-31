"""M13.1 Alpaca paper adapter contract tests (mocked HTTP; no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from broker_interface.alpaca import (
    ALPACA_PAPER_BASE_URL,
    AlpacaBroker,
    AlpacaBrokerConfig,
    alpaca_paper_broker_from_settings,
)
from broker_interface.broker import Broker, PaperBroker
from broker_interface.execution import ExecutionStatus
from broker_interface.http_transport import HttpResponse
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import OrderType, Side, Symbol
from portfolio_manager.portfolio import Portfolio
from runtime.factory import create_trading_runtime


class _FakeTransport:
    """Scripted HTTP transport for deterministic Alpaca adapter tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._queue: list[HttpResponse | BaseException] = []

    def push(self, response: HttpResponse | BaseException) -> None:
        self._queue.append(response)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method.upper(),
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
            }
        )
        if not self._queue:
            raise AssertionError(f"unexpected HTTP call {method} {url}")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _json_response(status: int, payload: object) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _broker(transport: _FakeTransport, **overrides: object) -> AlpacaBroker:
    cfg: dict[str, object] = {
        "api_key_id": "PKTESTKEY1234567890",
        "api_secret_key": "SECRETKEYVALUE1234567890",
        "base_url": ALPACA_PAPER_BASE_URL,
        "data_base_url": "https://data.alpaca.markets",
        "timeout_seconds": 5.0,
    }
    cfg.update(overrides)
    return AlpacaBroker(
        AlpacaBrokerConfig(**cfg),  # type: ignore[arg-type]
        transport=transport,
    )


def _market_buy(**overrides: object) -> BrokerOrderRequest:
    values: dict[str, object] = {
        "symbol": Symbol("AAPL"),
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("1"),
        "limit_price": None,
        "client_order_id": None,
    }
    values.update(overrides)
    return BrokerOrderRequest(**values)  # type: ignore[arg-type]


def _connect(broker: AlpacaBroker, transport: _FakeTransport) -> None:
    transport.push(
        _json_response(200, {"id": "acct-1", "buying_power": "100000.00"})
    )
    assert broker.connect() is True


def test_alpaca_broker_is_broker_subclass() -> None:
    broker = _broker(_FakeTransport())
    assert isinstance(broker, Broker)
    assert not isinstance(broker, PaperBroker)


def test_alpaca_rejects_live_api_host() -> None:
    with pytest.raises(ConfigurationError, match="live API host"):
        AlpacaBroker(
            AlpacaBrokerConfig(
                api_key_id="PK",
                api_secret_key="SK",
                base_url="https://api.alpaca.markets",
            ),
            transport=_FakeTransport(),
        )


def test_alpaca_requires_credentials() -> None:
    with pytest.raises(ConfigurationError, match="non-empty"):
        AlpacaBroker(
            AlpacaBrokerConfig(api_key_id="", api_secret_key="SK"),
            transport=_FakeTransport(),
        )


def test_auth_headers_use_apca_keys_without_logging_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "SUPER_SECRET_VALUE_XYZ"
    broker = _broker(_FakeTransport(), api_secret_key=secret)
    headers = broker.auth_headers()
    assert headers["APCA-API-KEY-ID"] == "PKTESTKEY1234567890"
    assert headers["APCA-API-SECRET-KEY"] == secret
    assert secret not in caplog.text


def test_connect_and_get_status_parse_account() -> None:
    transport = _FakeTransport()
    transport.push(
        _json_response(
            200,
            {
                "id": "acct-1",
                "account_number": "PA123",
                "buying_power": "250000.50",
                "status": "ACTIVE",
            },
        )
    )
    transport.push(
        _json_response(
            200,
            {
                "id": "acct-1",
                "buying_power": "249000.00",
                "status": "ACTIVE",
            },
        )
    )
    broker = _broker(transport)
    assert broker.connect() is True
    status = broker.get_status()
    assert status.connected is True
    assert status.account_id == "acct-1"
    assert status.buying_power == Decimal("249000.00")
    assert status.broker_name == "alpaca_paper"
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"].endswith("/v2/account")
    assert transport.calls[0]["headers"]["APCA-API-KEY-ID"] == "PKTESTKEY1234567890"
    assert transport.calls[0]["headers"]["APCA-API-SECRET-KEY"] == (
        "SECRETKEYVALUE1234567890"
    )


def test_place_order_happy_path_filled() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    _connect(broker, transport)
    transport.push(
        _json_response(
            200,
            {
                "id": "ord-filled-1",
                "status": "filled",
                "filled_qty": "2",
                "filled_avg_price": "190.25",
                "symbol": "AAPL",
                "side": "buy",
            },
        )
    )
    result = broker.place_order(
        _market_buy(quantity=Decimal("2"), client_order_id="client-1")
    )
    assert result.status is ExecutionStatus.FILLED
    assert str(result.order_id) == "ord-filled-1"
    assert result.filled_quantity == Decimal("2")
    assert result.fill_price == Decimal("190.2500")
    post = transport.calls[1]
    assert post["method"] == "POST"
    assert post["url"].endswith("/v2/orders")
    body = json.loads(post["body"].decode("utf-8"))
    assert body == {
        "symbol": "AAPL",
        "qty": "2",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "client-1",
    }


def test_place_order_rejected_path() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    _connect(broker, transport)
    transport.push(
        _json_response(
            200,
            {
                "id": "ord-rej-1",
                "status": "rejected",
                "reject_reason": "insufficient buying power",
                "filled_qty": "0",
            },
        )
    )
    result = broker.place_order(_market_buy())
    assert result.status is ExecutionStatus.REJECTED
    assert result.filled_quantity == Decimal("0")
    assert "insufficient buying power" in result.message


def test_place_order_http_error_status_rejected() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    _connect(broker, transport)
    transport.push(
        HttpResponse(
            status_code=422,
            body=b'{"message":"invalid order"}',
            headers={},
        )
    )
    result = broker.place_order(_market_buy())
    assert result.status is ExecutionStatus.REJECTED
    assert "422" in result.message
    assert "SECRETKEYVALUE" not in result.message


def test_get_order_status_parsing() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    transport.push(
        _json_response(
            200,
            {
                "id": "ord-42",
                "status": "filled",
                "filled_qty": "1",
                "filled_avg_price": "10.5",
            },
        )
    )
    payload = broker.get_order("ord-42")
    assert payload["id"] == "ord-42"
    assert payload["status"] == "filled"
    assert transport.calls[0]["url"].endswith("/v2/orders/ord-42")


def test_malformed_json_account_response_fails_connect() -> None:
    transport = _FakeTransport()
    transport.push(
        HttpResponse(status_code=200, body=b"not-json", headers={})
    )
    broker = _broker(transport)
    assert broker.connect() is False
    assert broker.get_status().connected is False


def test_timeout_network_failure_on_place_order() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    _connect(broker, transport)
    transport.push(TimeoutError("simulated timeout"))
    result = broker.place_order(_market_buy())
    assert result.status is ExecutionStatus.REJECTED
    assert "timeout" in result.message.lower() or "network" in result.message.lower()


def test_disconnected_place_order_rejects_without_http() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    result = broker.place_order(_market_buy())
    assert result.status is ExecutionStatus.REJECTED
    assert "disconnected" in result.message.lower()
    assert transport.calls == []


def test_open_accepted_order_fail_closed_not_invented_fill() -> None:
    transport = _FakeTransport()
    broker = _broker(transport)
    _connect(broker, transport)
    transport.push(
        _json_response(
            200,
            {
                "id": "ord-open",
                "status": "accepted",
                "filled_qty": "0",
                "filled_avg_price": None,
            },
        )
    )
    result = broker.place_order(_market_buy())
    assert result.status is ExecutionStatus.REJECTED
    assert "not filled" in result.message.lower()


def test_client_order_id_optional_on_request_dto() -> None:
    request = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
    )
    assert request.client_order_id is None
    request2 = BrokerOrderRequest(
        symbol=Symbol("AAPL"),
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        client_order_id="abc",
    )
    assert request2.client_order_id == "abc"


def test_alpaca_paper_broker_from_settings_uses_paper_url() -> None:
    transport = _FakeTransport()
    settings = Settings(
        broker_api_key="PKFROMENV",
        broker_api_secret="SKFROMENV",
        broker_base_url="https://paper-api.example.com",
        broker_name="alpaca_paper",
    )
    broker = alpaca_paper_broker_from_settings(settings, transport=transport)
    assert broker.base_url == ALPACA_PAPER_BASE_URL
    assert broker.auth_headers()["APCA-API-KEY-ID"] == "PKFROMENV"


def test_factory_still_rejects_non_paper_broker() -> None:
    """M13.1: Alpaca exists but production/live factory path stays CLOSED."""
    transport = _FakeTransport()
    alpaca = _broker(transport)
    with pytest.raises(ConfigurationError, match="PaperBroker"):
        create_trading_runtime(
            Settings(trading_mode="paper", market_data_provider="mock"),
            execution="paper",
            market_data=object(),
            strategy_engine=object(),
            portfolio=Portfolio(cash=Decimal("100000")),
            broker=alpaca,  # type: ignore[arg-type]
            with_alerts=False,
        )


def test_factory_still_rejects_trading_mode_live() -> None:
    with pytest.raises(ConfigurationError, match="live"):
        create_trading_runtime(
            Settings(trading_mode="live", market_data_provider="mock"),
            execution="dry_run",
            market_data=object(),
            strategy_engine=object(),
            portfolio=Portfolio(cash=Decimal("100000")),
            with_alerts=False,
        )
