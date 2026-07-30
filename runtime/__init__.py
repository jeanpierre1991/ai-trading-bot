"""Trading runtime package."""

from __future__ import annotations

from broker_interface.execution import ExecutionResult, ExecutionStatus
from runtime.base import TradingRuntime
from runtime.broker_executor import BrokerOrderExecutor
from runtime.context import RuntimeContext
from runtime.dry_run import DryRunExecutor
from runtime.executor import OrderExecutor
from runtime.factory import create_trading_runtime, create_trading_runtime_from_app
from runtime.fills import execution_to_fill, is_bookable
from runtime.models import PipelineResult, TradeIntent
from runtime.risk_gate import RiskGateResult, apply_risk_gate
from runtime.kill_switch import FileEnvKillSwitch, KillSwitch
from runtime.paper_operator import (
    MAX_OPERATOR_CYCLES,
    PaperOperator,
    PaperOperatorConfig,
    PaperOperatorResult,
)
from runtime.paper_state import OPERATOR_STATE_SCHEMA_VERSION, OperatorState
from runtime.paper_state_store import JsonPaperStateStore, PaperStateStore
from runtime.session import (
    MAX_SESSION_CYCLES,
    SessionConfig,
    SessionResult,
    SessionRunner,
    compute_session_pnl_pct,
)
from runtime.trading_runtime import BasicTradingRuntime

__all__ = [
    "MAX_OPERATOR_CYCLES",
    "MAX_SESSION_CYCLES",
    "BasicTradingRuntime",
    "BrokerOrderExecutor",
    "DryRunExecutor",
    "ExecutionResult",
    "ExecutionStatus",
    "FileEnvKillSwitch",
    "JsonPaperStateStore",
    "KillSwitch",
    "OPERATOR_STATE_SCHEMA_VERSION",
    "OperatorState",
    "OrderExecutor",
    "PaperOperator",
    "PaperOperatorConfig",
    "PaperOperatorResult",
    "PaperStateStore",
    "PipelineResult",
    "RiskGateResult",
    "RuntimeContext",
    "SessionConfig",
    "SessionResult",
    "SessionRunner",
    "TradeIntent",
    "TradingRuntime",
    "apply_risk_gate",
    "compute_session_pnl_pct",
    "create_trading_runtime",
    "create_trading_runtime_from_app",
    "execution_to_fill",
    "is_bookable",
]
