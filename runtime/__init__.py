"""Trading runtime package."""

from __future__ import annotations

from broker_interface.execution import ExecutionResult, ExecutionStatus
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.models import PipelineResult, TradeIntent
from runtime.risk_gate import RiskGateResult, apply_risk_gate
from runtime.trading_runtime import BasicTradingRuntime

__all__ = [
    "BasicTradingRuntime",
    "DryRunExecutor",
    "ExecutionResult",
    "ExecutionStatus",
    "OrderExecutor",
    "PipelineResult",
    "RiskGateResult",
    "RuntimeContext",
    "TradeIntent",
    "TradingRuntime",
    "apply_risk_gate",
]
