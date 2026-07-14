"""Strategy abstractions shared by all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.types import MarketBar
from strategy_engine.signal import StrategySignal


class BaseStrategy(ABC):
    """Contract every strategy must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier used by the registry."""

    @abstractmethod
    def evaluate(self, bars: list[MarketBar], *, symbol: str) -> StrategySignal:
        """Evaluate market bars and return a trading signal."""
