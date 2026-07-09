"""Strategy engine module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import SignalAction
from strategy_engine.signal import StrategySignal


class StrategyEngineModule(BaseModule):
    """Coordinates strategy evaluation (logic to be implemented in milestone 2)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._registered_strategies: list[str] = []

    @property
    def name(self) -> str:
        return "strategy_engine"

    def _on_initialize(self) -> None:
        self._registered_strategies = ["momentum", "mean_reversion", "ai_hybrid"]

    def list_strategies(self) -> list[str]:
        return list(self._registered_strategies)

    def generate_placeholder_signal(self, symbol: str | None = None) -> StrategySignal:
        """Returns a neutral signal for architecture verification only."""
        return StrategySignal(
            symbol=symbol or self._settings.default_symbol,
            action=SignalAction.HOLD,
            confidence=0.0,
            strategy_name="architecture_check",
            price=Decimal("0"),
            metadata={"note": "Trading logic not yet implemented"},
        )

    def health_check(self) -> ModuleHealth:
        try:
            signal = self.generate_placeholder_signal()
            return self._healthy(
                message="Strategy engine ready (awaiting trading logic)",
                strategies=self._registered_strategies,
                sample_signal=signal.to_dict(),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
