"""Broker connectivity and order execution."""

from __future__ import annotations

from broker_interface.broker import Broker, BrokerStatus, PaperBroker
from broker_interface.execution import ExecutionResult, ExecutionStatus
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
    "BrokerStatus",
    "ClosedBarQuoteSource",
    "QuoteSource",
    "QuoteUnavailableError",
    "ExecutionResult",
    "ExecutionStatus",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(BrokerInterfaceModule)
