"""Runtime execution context for a single trading cycle."""

from __future__ import annotations

from dataclasses import dataclass

from core.types import TradingMode


@dataclass(frozen=True)
class RuntimeContext:
    """Minimal inputs required to run one TradingRuntime cycle."""

    symbol: str
    mode: TradingMode = TradingMode.PAPER
    strategy_name: str | None = None
    bar_limit: int = 100
    portfolio_value: float | None = None
