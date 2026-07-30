"""Runtime execution context for a single trading cycle."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.types import TradingMode


@dataclass(frozen=True)
class RuntimeContext:
    """Minimal inputs required to run one TradingRuntime cycle."""

    symbol: str
    mode: TradingMode = TradingMode.PAPER
    strategy_name: str | None = None
    bar_limit: int = 100
    portfolio_value: float | None = None
    # Pre-computed daily P&L fraction from an external source (e.g. -0.02 = -2%).
    # Required for actionable trades when max_daily_loss_pct is active (fail-closed).
    daily_pnl_pct: Decimal | None = None
    # M11.2: None → use settings.market_data_freshness_enabled; False for M10 backtests.
    enforce_market_data_freshness: bool | None = None
    # M11.3: None → use settings.market_hours_enabled; False for M10 backtests.
    enforce_market_hours: bool | None = None
    # M12.2 E1: canonical UTC ISO of an already-actioned bar for this symbol.
    # Compared only to the bar timestamp from this cycle's market-data snapshot.
    # When equal, side effects (order/execution/booking) are skipped AFTER risk.
    already_actioned_bar_timestamp: str | None = None
