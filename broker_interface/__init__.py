"""Broker connectivity and order execution."""

from __future__ import annotations

from broker_interface.broker import Broker, BrokerStatus, PaperBroker
from broker_interface.module import BrokerInterfaceModule

MODULE_CLASS = BrokerInterfaceModule

__all__ = ["BrokerInterfaceModule", "Broker", "PaperBroker", "BrokerStatus", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(BrokerInterfaceModule)
