"""Broker interface module implementation."""

from __future__ import annotations

from broker_interface.broker import Broker, PaperBroker
from broker_interface.execution import ExecutionResult
from broker_interface.orders import BrokerOrderRequest
from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import Symbol


class BrokerInterfaceModule(BaseModule):
    """Manages broker connections and order routing."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._broker: Broker | None = None

    @property
    def name(self) -> str:
        return "broker_interface"

    def _on_initialize(self) -> None:
        broker_name = self._settings.broker_name.lower()
        if broker_name in {"paper", "mock"}:
            self._broker = PaperBroker(
                name=broker_name,
                buying_power=self._settings.backtest_initial_capital,
            )
        else:
            self._broker = PaperBroker(name=broker_name)
            self.logger.warning("Broker '%s' not implemented; using paper broker", broker_name)

        self._broker.connect()

    def _on_shutdown(self) -> None:
        if self._broker is not None:
            self._broker.disconnect()

    @property
    def broker(self) -> Broker:
        if self._broker is None:
            raise RuntimeError("Broker not initialized")
        return self._broker

    def place_order(self, request: BrokerOrderRequest) -> ExecutionResult:
        """Delegate order placement to the active broker adapter."""
        return self.broker.place_order(request)

    def health_check(self) -> ModuleHealth:
        if self._broker is None:
            return self._unhealthy("Broker not initialized")

        try:
            status = self._broker.get_status()
            quote = self._broker.get_quote(Symbol(self._settings.default_symbol))
            return self._healthy(
                message="Broker connection operational",
                broker=status.broker_name,
                connected=status.connected,
                account_id=status.account_id,
                buying_power=str(status.buying_power),
                sample_quote=str(quote),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
