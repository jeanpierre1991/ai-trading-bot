"""Strategy engine module implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import MarketBar
from strategy_engine.base import BaseStrategy
from strategy_engine.registry import create_strategy, list_strategies
from strategy_engine.signal import StrategySignal


class StrategyEngineModule(BaseModule):
    """Coordinates strategy registration and evaluation."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._registered_strategies: list[str] = []
        self._strategies: dict[str, BaseStrategy] = {}

    @property
    def name(self) -> str:
        return "strategy_engine"

    def _on_initialize(self) -> None:
        self._strategies = {
            strategy_name: create_strategy(strategy_name)
            for strategy_name in list_strategies()
        }
        self._registered_strategies = list(self._strategies.keys())

    def list_strategies(self) -> list[str]:
        return list(self._registered_strategies)

    def get_strategy(self, name: str) -> BaseStrategy:
        try:
            return self._strategies[name.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"Unknown strategy: '{name}'") from exc

    def evaluate(
        self,
        bars: list[MarketBar],
        *,
        symbol: str | None = None,
        strategy_name: str = "ema_crossover",
    ) -> StrategySignal:
        if not self._strategies:
            raise RuntimeError("Strategy engine not initialized")

        strategy = self.get_strategy(strategy_name)
        resolved_symbol = symbol or self._settings.default_symbol
        return strategy.evaluate(bars, symbol=resolved_symbol)

    def health_check(self) -> ModuleHealth:
        if not self._strategies:
            return self._unhealthy("Strategies not initialized")

        try:
            signal = self.evaluate(self._sample_bars())
            return self._healthy(
                message="Strategy engine operational",
                strategies=self._registered_strategies,
                sample_signal=signal.to_dict(),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")

    @staticmethod
    def _sample_bars(count: int = 60) -> list[MarketBar]:
        """Deterministic OHLCV sample long enough for default EMA periods."""
        base_timestamp = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        bars: list[MarketBar] = []
        price = Decimal("100")

        for index in range(count):
            # Mild uptrend then a soft pullback so EMA series are well-defined.
            if index < 40:
                price = (price + Decimal("0.35")).quantize(Decimal("0.01"))
            else:
                price = (price - Decimal("0.20")).quantize(Decimal("0.01"))

            bars.append(
                MarketBar(
                    timestamp=base_timestamp + timedelta(hours=index),
                    open=price,
                    high=price + Decimal("0.50"),
                    low=price - Decimal("0.50"),
                    close=price,
                    volume=Decimal("1000"),
                    symbol="AAPL",
                    timeframe="1h",
                )
            )

        return bars
