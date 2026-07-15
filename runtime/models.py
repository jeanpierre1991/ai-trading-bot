"""Runtime domain models for the trading pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from broker_interface.execution import ExecutionResult
from core.types import OrderType, Side, Symbol
from order_manager.manager import OrderRecord
from risk_manager.rules import RiskAssessment
from strategy_engine.signal import StrategySignal


@dataclass(frozen=True)
class TradeIntent:
    """Executable trade intention produced after strategy + risk evaluation."""

    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    strategy_name: str
    signal_confidence: float
    max_position_value: Decimal
    reason: str


@dataclass(frozen=True)
class PipelineResult:
    """Aggregated outcome of a single TradingRuntime.run_once cycle."""

    success: bool
    stage_reached: str
    aborted_reason: str | None = None
    signal: StrategySignal | None = None
    risk_assessment: RiskAssessment | None = None
    intent: TradeIntent | None = None
    order: OrderRecord | None = None
    execution: ExecutionResult | None = None
    portfolio_snapshot: dict[str, float | int] | None = None
    alerts_sent: int = 0
