"""Alpaca paper/sandbox ``Broker`` adapter (Milestone 13.1).

Implements the broker-agnostic ``Broker`` contract against Alpaca's paper API.
Does not open the factory/live enablement path (M13.2). Production live host
``api.alpaca.markets`` is rejected at construction time.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlparse

from broker_interface.broker import Broker, BrokerStatus
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.http_transport import HttpTransport, UrllibHttpTransport
from broker_interface.orders import BrokerOrderRequest
from core.exceptions import ConfigurationError
from core.types import OrderId, OrderType, Side, Symbol

_logger = logging.getLogger("trading_bot.broker.alpaca")

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_API_HOST = "api.alpaca.markets"
ALPACA_PAPER_API_HOST = "paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"

_PRICE = Decimal("0.0001")
_QTY = Decimal("0.00000001")


def _redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


@dataclass(frozen=True)
class AlpacaBrokerConfig:
    """Construction settings for the Alpaca paper adapter.

    Credentials must come from environment/settings — never hard-code secrets.
    """

    api_key_id: str
    api_secret_key: str
    base_url: str = ALPACA_PAPER_BASE_URL
    data_base_url: str = ALPACA_DATA_BASE_URL
    timeout_seconds: float = 10.0
    broker_name: str = "alpaca_paper"


class AlpacaBroker(Broker):
    """Alpaca paper/sandbox broker implementing the shared ``Broker`` port."""

    def __init__(
        self,
        config: AlpacaBrokerConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        key = str(config.api_key_id or "").strip()
        secret = str(config.api_secret_key or "").strip()
        if not key or not secret:
            raise ConfigurationError(
                "AlpacaBroker requires non-empty api_key_id and api_secret_key"
            )

        self._base_url = _validate_paper_base_url(config.base_url)
        self._data_base_url = str(config.data_base_url or ALPACA_DATA_BASE_URL).strip().rstrip(
            "/"
        )
        self._key_id = key
        self._secret = secret
        self._timeout = float(config.timeout_seconds)
        if self._timeout <= 0 or self._timeout != self._timeout:
            raise ConfigurationError(
                f"AlpacaBroker timeout_seconds must be a positive finite number; "
                f"got {config.timeout_seconds!r}"
            )
        self._name = str(config.broker_name or "alpaca_paper")
        self._transport: HttpTransport = (
            UrllibHttpTransport() if transport is None else transport
        )
        self._connected = False
        self._account_id = ""
        self._buying_power = Decimal("0")

        _logger.info(
            "AlpacaBroker configured base_url=%s key_id=%s",
            self._base_url,
            _redact(self._key_id),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def broker_name(self) -> str:
        return self._name

    def auth_headers(self) -> dict[str, str]:
        """Return Alpaca auth headers (for tests). Never log the secret value."""
        return {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def connect(self) -> bool:
        try:
            payload = self._request_json("GET", f"{self._base_url}/v2/account")
        except Exception as exc:
            _logger.warning("Alpaca connect failed: %s", _safe_exc(exc))
            self._connected = False
            return False
        self._apply_account_payload(payload)
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def get_status(self) -> BrokerStatus:
        if not self._connected:
            return BrokerStatus(
                connected=False,
                broker_name=self._name,
                account_id=self._account_id or "",
                buying_power=self._buying_power,
                checked_at=datetime.now(timezone.utc),
            )
        try:
            payload = self._request_json("GET", f"{self._base_url}/v2/account")
            self._apply_account_payload(payload)
        except Exception as exc:
            _logger.warning("Alpaca get_status failed: %s", _safe_exc(exc))
            self._connected = False
        return BrokerStatus(
            connected=self._connected,
            broker_name=self._name,
            account_id=self._account_id,
            buying_power=self._buying_power,
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        symbol_text = str(symbol).strip().upper()
        if not symbol_text:
            raise ConfigurationError("symbol must be a non-empty string")
        url = f"{self._data_base_url}/v2/stocks/{symbol_text}/trades/latest"
        payload = self._request_json("GET", url)
        trade = payload.get("trade") if isinstance(payload, dict) else None
        if not isinstance(trade, dict):
            raise ConfigurationError(
                f"Alpaca quote response missing trade for {symbol_text}"
            )
        price = _decimal_field(trade.get("p"), field_name="trade.p")
        if price <= 0:
            raise ConfigurationError(
                f"Alpaca quote price must be positive for {symbol_text}"
            )
        return price.quantize(_PRICE)

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        symbol_text = str(request.symbol).strip() if request.symbol is not None else ""
        side = request.side if isinstance(request.side, Side) else None
        quantity = request.quantity if request.quantity is not None else Decimal("0")

        if not self._connected:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side or Side.BUY,
                quantity=quantity,
                message="Broker is disconnected",
            )
        if not symbol_text:
            return self._reject(
                symbol=Symbol(""),
                side=side or Side.BUY,
                quantity=quantity,
                message="Symbol must be a non-empty string",
            )
        if side not in (Side.BUY, Side.SELL):
            return self._reject(
                symbol=Symbol(symbol_text),
                side=Side.BUY,
                quantity=quantity,
                message=f"Invalid side for Alpaca order: {request.side!r}",
            )
        if quantity <= 0:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message="Quantity must be positive",
            )
        if request.order_type is not OrderType.MARKET:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=(
                    f"Unsupported order_type for Alpaca adapter: "
                    f"{request.order_type!r} (only MARKET in M13.1)"
                ),
            )

        body: dict[str, Any] = {
            "symbol": symbol_text.upper(),
            "qty": format(quantity, "f"),
            "side": "buy" if side is Side.BUY else "sell",
            "type": "market",
            "time_in_force": "day",
        }
        if request.client_order_id:
            body["client_order_id"] = str(request.client_order_id)

        try:
            payload = self._request_json(
                "POST",
                f"{self._base_url}/v2/orders",
                body=body,
            )
        except TimeoutError as exc:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=f"Alpaca order transport timeout/network failure: {_safe_exc(exc)}",
            )
        except Exception as exc:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=f"Alpaca order request failed: {_safe_exc(exc)}",
            )

        if not isinstance(payload, dict):
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message="Alpaca order response was not a JSON object",
            )

        return self._map_order_payload(
            payload,
            symbol=Symbol(symbol_text),
            side=side,
            quantity=quantity,
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch a raw venue order payload (adapter helper for status parsing tests)."""
        oid = str(order_id).strip()
        if not oid:
            raise ConfigurationError("order_id must be non-empty")
        payload = self._request_json("GET", f"{self._base_url}/v2/orders/{oid}")
        if not isinstance(payload, dict):
            raise ConfigurationError("Alpaca order status response was not a JSON object")
        return payload

    def _map_order_payload(
        self,
        payload: Mapping[str, Any],
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
    ) -> ExecutionResult:
        status_text = str(payload.get("status", "")).strip().lower()
        order_id = str(payload.get("id") or uuid.uuid4())
        filled_qty = _decimal_field(
            payload.get("filled_qty", "0"),
            field_name="filled_qty",
            default=Decimal("0"),
        )
        fill_price_raw = payload.get("filled_avg_price")
        fill_price = (
            _decimal_field(fill_price_raw, field_name="filled_avg_price", default=Decimal("0"))
            if fill_price_raw not in (None, "")
            else Decimal("0")
        )

        if status_text in {"rejected", "canceled", "cancelled", "expired", "suspended"}:
            message = str(payload.get("reject_reason") or payload.get("status") or "rejected")
            return self._reject(
                symbol=symbol,
                side=side,
                quantity=quantity,
                message=f"Alpaca order {status_text}: {message}",
                order_id=OrderId(order_id),
            )

        if status_text == "filled" or (
            filled_qty > 0 and status_text in {"partially_filled", "done_for_day"}
        ):
            if filled_qty <= 0 or fill_price <= 0:
                return self._reject(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    message=(
                        "Alpaca order reported filled without valid "
                        "filled_qty/filled_avg_price"
                    ),
                    order_id=OrderId(order_id),
                )
            return ExecutionResult(
                order_id=OrderId(order_id),
                symbol=symbol,
                side=side,
                requested_quantity=quantity,
                filled_quantity=filled_qty.quantize(_QTY),
                fill_price=fill_price.quantize(_PRICE),
                fee=Decimal("0"),
                status=ExecutionStatus.FILLED,
                message=f"Alpaca order {status_text}",
            )

        # Fail closed: do not invent fills for accepted/new/open states.
        return self._reject(
            symbol=symbol,
            side=side,
            quantity=quantity,
            message=(
                f"Alpaca order not filled (status={status_text or 'unknown'}); "
                "M13.1 maps only definitive fills"
            ),
            order_id=OrderId(order_id),
        )

    def _apply_account_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ConfigurationError("Alpaca account response was not a JSON object")
        self._account_id = str(payload.get("id") or payload.get("account_number") or "")
        self._buying_power = _decimal_field(
            payload.get("buying_power", "0"),
            field_name="buying_power",
            default=Decimal("0"),
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        raw_body = None if body is None else json.dumps(body).encode("utf-8")
        response = self._transport.request(
            method,
            url,
            headers=self.auth_headers(),
            body=raw_body,
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            # Never include auth headers or secrets in the message.
            detail = _safe_response_detail(response.text())
            raise ConfigurationError(
                f"Alpaca API error status={response.status_code} detail={detail}"
            )
        try:
            return response.json()
        except Exception as exc:
            raise ConfigurationError(
                f"Alpaca API returned non-JSON body (status={response.status_code})"
            ) from exc

    def _reject(
        self,
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
        message: str,
        order_id: OrderId | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            order_id=order_id or OrderId(str(uuid.uuid4())),
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )


def _validate_paper_base_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        raise ConfigurationError("Alpaca base_url must be non-empty")
    host = (urlparse(text).hostname or "").lower()
    if host == ALPACA_LIVE_API_HOST:
        raise ConfigurationError(
            "Alpaca live API host api.alpaca.markets is not allowed in M13.1; "
            "use paper-api.alpaca.markets (paper/sandbox only)"
        )
    if host != ALPACA_PAPER_API_HOST:
        raise ConfigurationError(
            "AlpacaBroker M13.1 allows only paper/sandbox host "
            f"{ALPACA_PAPER_API_HOST!r}; got {host!r}"
        )
    return text


def _decimal_field(
    value: Any,
    *,
    field_name: str,
    default: Decimal | None = None,
) -> Decimal:
    if value is None or value == "":
        if default is not None:
            return default
        raise ConfigurationError(f"Alpaca field {field_name} missing")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(
            f"Alpaca field {field_name} is not a valid decimal: {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise ConfigurationError(f"Alpaca field {field_name} must be finite")
    return parsed


def _safe_exc(exc: BaseException) -> str:
    text = str(exc)
    # Defense in depth: never echo credential-looking material.
    lowered = text.lower()
    for token in ("apca-api-secret", "secret_key", "api_secret"):
        if token in lowered:
            return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text}"


def _safe_response_detail(text: str, *, limit: int = 240) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


def alpaca_paper_broker_from_settings(
    settings: Any,
    *,
    transport: HttpTransport | None = None,
) -> AlpacaBroker:
    """Build an Alpaca paper/sandbox adapter from Settings.

    Uses ``broker_api_key`` / ``broker_api_secret`` / ``broker_base_url``.
    If ``broker_base_url`` is the legacy placeholder, substitutes the Alpaca
    paper URL. Live host remains rejected by ``AlpacaBroker`` construction.
    Factory wires this only under M13.2 ``execution='live'`` + all gates.
    """
    # Local import keeps settings dependency optional for pure adapter unit tests.
    base = str(getattr(settings, "broker_base_url", "") or "").strip()
    if not base or "example.com" in base:
        base = ALPACA_PAPER_BASE_URL
    return AlpacaBroker(
        AlpacaBrokerConfig(
            api_key_id=str(getattr(settings, "broker_api_key", "") or ""),
            api_secret_key=str(getattr(settings, "broker_api_secret", "") or ""),
            base_url=base,
            broker_name=str(getattr(settings, "broker_name", "") or "alpaca_paper"),
        ),
        transport=transport,
    )
