"""Order manager module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import OrderType, Side, Symbol
from order_manager.manager import OrderManager, OrderState


class OrderManagerModule(BaseModule):
    """Manages order creation, tracking, and lifecycle."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._manager: OrderManager | None = None

    @property
    def name(self) -> str:
        return "order_manager"

    def _on_initialize(self) -> None:
        self._manager = OrderManager()

    @property
    def manager(self) -> OrderManager:
        if self._manager is None:
            raise RuntimeError("Order manager not initialized")
        return self._manager

    def health_check(self) -> ModuleHealth:
        if self._manager is None:
            return self._unhealthy("Order manager not initialized")

        try:
            order = self._manager.create_order(
                symbol=Symbol("AAPL"),
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("10"),
                price=Decimal("190.00"),
            )
            self._manager.update_state(str(order.order_id), OrderState.SUBMITTED)
            return self._healthy(
                message="Order manager operational",
                orders_tracked=self._manager.order_count,
                sample_order=order.to_dict(),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
