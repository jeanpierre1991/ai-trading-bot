"""EMA crossover strategy scaffold (Milestone 3 structure).

Trading logic is intentionally not implemented in this class yet.
The production implementation remains in ``EmaCrossoverStrategy``.
"""

from __future__ import annotations

from core.types import MarketBar
from strategy_engine.base import Strategy
from strategy_engine.signal import StrategySignal


class EMACrossoverStrategy(Strategy):
    """Base structure for the EMA crossover strategy (logic pending)."""

    def __init__(self, fast_period: int = 12, slow_period: int = 26) -> None:
        if fast_period <= 0 or slow_period <= 0:
            raise ValueError("EMA periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")

        self._fast_period = fast_period
        self._slow_period = slow_period

    @property
    def name(self) -> str:
        return "ema_crossover"

    @property
    def fast_period(self) -> int:
        return self._fast_period

    @property
    def slow_period(self) -> int:
        return self._slow_period

    def evaluate(self, bars: list[MarketBar], *, symbol: str) -> StrategySignal:
        raise NotImplementedError(
            "EMACrossoverStrategy trading logic is not implemented yet"
        )
