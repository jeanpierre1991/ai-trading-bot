"""Order lifecycle management."""

from __future__ import annotations

from order_manager.manager import OrderManager, OrderRecord, OrderState
from order_manager.module import OrderManagerModule

MODULE_CLASS = OrderManagerModule

__all__ = ["OrderManagerModule", "OrderManager", "OrderRecord", "OrderState", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(OrderManagerModule)
