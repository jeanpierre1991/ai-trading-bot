"""Broker abstractions and paper trading implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from core.types import Symbol


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


class PaperBroker(Broker):
    """Simulated broker for paper trading."""

    def __init__(self, name: str = "paper", buying_power: Decimal = Decimal("100000")) -> None:
        self._name = name
        self._buying_power = buying_power
        self._connected = False
        self._account_id = "PAPER-001"

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
        quotes = {
            "AAPL": Decimal("190.25"),
            "MSFT": Decimal("420.50"),
            "GOOGL": Decimal("175.10"),
            "SPY": Decimal("520.75"),
        }
        return quotes.get(symbol, Decimal("100.00"))
