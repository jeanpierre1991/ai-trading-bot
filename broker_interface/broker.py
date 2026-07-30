"""Broker abstractions and paper trading implementation."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.orders import BrokerOrderRequest
from broker_interface.quotes import QuoteSource, QuoteUnavailableError
from core.types import OrderId, OrderType, Side, Symbol

_MONEY = Decimal("0.01")
_PRICE = Decimal("0.0001")

_LEGACY_QUOTES: dict[str, Decimal] = {
    "AAPL": Decimal("190.25"),
    "MSFT": Decimal("420.50"),
    "GOOGL": Decimal("175.10"),
    "SPY": Decimal("520.75"),
}
_LEGACY_DEFAULT_QUOTE = Decimal("100.00")


@dataclass(frozen=True)
class BrokerStatus:
    connected: bool
    broker_name: str
    account_id: str
    buying_power: Decimal
    checked_at: datetime


class Broker(ABC):
    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def get_status(self) -> BrokerStatus:
        ...

    @abstractmethod
    def get_quote(self, symbol: Symbol) -> Decimal:
        ...

    @abstractmethod
    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        """Submit an order to the broker and return an execution result."""
        ...


class PaperBroker(Broker):
    """Simulated broker for paper trading (local only, no network).

    When ``quote_source`` is injected (M11.1), MARKET fills use that source's
    closed-bar price exclusively. Missing/invalid quotes reject the order —
    there is no silent fallback to the legacy static map.

    When ``quote_source`` is omitted, legacy static quotes are used for
    backward compatibility with existing unit tests.
    """

    def __init__(
        self,
        name: str = "paper",
        buying_power: Decimal = Decimal("100000"),
        *,
        quote_source: QuoteSource | None = None,
    ) -> None:
        self._name = name
        self._buying_power = buying_power
        self._connected = False
        self._account_id = "PAPER-001"
        self._quote_source = quote_source

    @property
    def quote_source(self) -> QuoteSource | None:
        return self._quote_source

    def set_quote_source(self, quote_source: QuoteSource | None) -> None:
        """Attach or clear the M11.1 quote source (factory wiring)."""
        self._quote_source = quote_source

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def get_status(self) -> BrokerStatus:
        return BrokerStatus(
            connected=self._connected,
            broker_name=self._name,
            account_id=self._account_id,
            buying_power=self._buying_power,
            checked_at=datetime.now(timezone.utc),
        )

    def get_quote(self, symbol: Symbol) -> Decimal:
        """Return the price used for paper MARKET fills.

        With a quote source: closed-bar price only (raises
        ``QuoteUnavailableError`` if unavailable/invalid).
        Without a quote source: legacy static map (backward compatible).
        """
        if self._quote_source is not None:
            return self._quote_source.get_closed_bar_price(symbol)
        return _LEGACY_QUOTES.get(str(symbol).strip(), _LEGACY_DEFAULT_QUOTE)

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        """Fill MARKET orders locally using ``get_quote`` as the execution price.

        Does not contact external APIs and does not mutate portfolio state.
        """
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
                message=f"Invalid side for paper order: {request.side!r}",
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
                    f"Unsupported order_type for paper broker: {request.order_type!r} "
                    "(only MARKET is supported)"
                ),
            )

        try:
            fill_price = self.get_quote(Symbol(symbol_text)).quantize(_PRICE)
        except QuoteUnavailableError as exc:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=f"Paper quote unavailable: {exc}",
            )

        if not fill_price.is_finite() or fill_price <= 0:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=f"Paper quote unavailable: invalid fill price {fill_price}",
            )

        notional = (quantity * fill_price).quantize(_MONEY)

        if side is Side.BUY and notional > self._buying_power:
            return self._reject(
                symbol=Symbol(symbol_text),
                side=side,
                quantity=quantity,
                message=(
                    f"Insufficient buying power: need {notional}, "
                    f"available {self._buying_power}"
                ),
            )

        if side is Side.BUY:
            self._buying_power = (self._buying_power - notional).quantize(_MONEY)
        else:
            self._buying_power = (self._buying_power + notional).quantize(_MONEY)

        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=Symbol(symbol_text),
            side=side,
            requested_quantity=quantity,
            filled_quantity=quantity,
            fill_price=fill_price,
            fee=Decimal("0"),
            status=ExecutionStatus.FILLED,
            message="Paper order filled",
        )

    def _reject(
        self,
        *,
        symbol: Symbol,
        side: Side,
        quantity: Decimal,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            order_id=OrderId(str(uuid.uuid4())),
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            fill_price=Decimal("0"),
            fee=Decimal("0"),
            status=ExecutionStatus.REJECTED,
            message=message,
        )
