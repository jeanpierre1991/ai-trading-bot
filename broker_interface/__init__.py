"""Broker connectivity and order execution.

Venue adapters (Alpaca today; IBKR/TradeStation/Webull later) implement
``Broker`` under ``broker_interface`` packages. Core runtime/strategy/risk/
portfolio code must depend only on the agnostic ``Broker`` port.
"""

from __future__ import annotations

from broker_interface.alpaca import (
    ALPACA_PAPER_BASE_URL,
    AlpacaBroker,
    AlpacaBrokerConfig,
)
from broker_interface.broker import Broker, BrokerStatus, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
from broker_interface.http_transport import HttpResponse, HttpTransport, UrllibHttpTransport
from broker_interface.module import BrokerInterfaceModule
from broker_interface.orders import BrokerOrderRequest
from broker_interface.quotes import (
    ClosedBarQuoteSource,
    QuoteSource,
    QuoteUnavailableError,
)

MODULE_CLASS = BrokerInterfaceModule

__all__ = [
    "BrokerInterfaceModule",
    "Broker",
    "BrokerOrderRequest",
    "PaperBroker",
    "AlpacaBroker",
    "AlpacaBrokerConfig",
    "ALPACA_PAPER_BASE_URL",
    "BrokerStatus",
    "ClosedBarQuoteSource",
    "QuoteSource",
    "QuoteUnavailableError",
    "ExecutionResult",
    "ExecutionStatus",
    "HttpResponse",
    "HttpTransport",
    "UrllibHttpTransport",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(BrokerInterfaceModule)
