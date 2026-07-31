"""Bounded paper/dry-run multi-cycle session orchestration (Milestone 9.1).

Runs N ``run_once`` cycles on a shared Runtime. Does not duplicate risk,
booking, mode, broker, alerts, or factory logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.models import PipelineResult

# Hard cap so sessions cannot become unbounded loops.
MAX_SESSION_CYCLES = 100


@dataclass(frozen=True)
class SessionConfig:
    """Inputs for one bounded paper/dry-run (or gated live sandbox) session."""

    cycles: int
    symbol: str
    strategy_name: str | None = None
    bar_limit: int = 100
    # M13.2: LIVE only when factory execution='live' and gates authorize sandbox.
    mode: TradingMode = TradingMode.PAPER


@dataclass(frozen=True)
class SessionResult:
    """Aggregated outcome of a SessionRunner.run call."""

    cycles_requested: int
    cycles_executed: int
    results: tuple[PipelineResult, ...]
    session_start_equity: Decimal
    session_end_equity: Decimal
    stopped_early: bool
    stop_reason: str | None

    @property
    def completed_all_cycles(self) -> bool:
        return (
            not self.stopped_early
            and self.cycles_executed == self.cycles_requested
        )


def compute_session_pnl_pct(
    session_start_equity: Decimal,
    current_equity: Decimal,
) -> Decimal:
    """Session equity change as a fraction of session-start equity.

    Mapped by SessionRunner to ``RuntimeContext.daily_pnl_pct`` so M8.2
    operational loss limits remain fail-closed (never ``None`` when cycling).
    """
    if session_start_equity <= 0:
        raise ConfigurationError(
            "session_start_equity must be positive to compute session_pnl_pct"
        )
    return (current_equity - session_start_equity) / session_start_equity


class SessionRunner:
    """Orchestrates bounded multi-cycle sessions on one TradingRuntime.

    The injected runtime (and its portfolio / optional OrderManager) is shared
    for the entire session. Each cycle calls ``runtime.run_once`` only.
    """

    def __init__(self, runtime: TradingRuntime) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> TradingRuntime:
        return self._runtime

    def run(self, config: SessionConfig) -> SessionResult:
        self._validate_config(config)

        portfolio = self._portfolio()
        session_start_equity = self._equity(portfolio)
        if session_start_equity <= 0:
            raise ConfigurationError(
                "session_start_equity must be positive; "
                f"got {session_start_equity}"
            )

        results: list[PipelineResult] = []
        stopped_early = False
        stop_reason: str | None = None

        for index in range(config.cycles):
            current_equity = self._equity(portfolio)
            session_pnl_pct = compute_session_pnl_pct(
                session_start_equity,
                current_equity,
            )
            context = RuntimeContext(
                symbol=config.symbol,
                mode=config.mode,
                strategy_name=config.strategy_name,
                bar_limit=config.bar_limit,
                daily_pnl_pct=session_pnl_pct,
            )
            result = self._runtime.run_once(context)
            results.append(result)

            if not result.success:
                stopped_early = True
                stop_reason = (
                    result.aborted_reason
                    or f"cycle {index} aborted at stage {result.stage_reached!r}"
                )
                break

        return SessionResult(
            cycles_requested=config.cycles,
            cycles_executed=len(results),
            results=tuple(results),
            session_start_equity=session_start_equity,
            session_end_equity=self._equity(portfolio),
            stopped_early=stopped_early,
            stop_reason=stop_reason,
        )

    def _validate_config(self, config: SessionConfig) -> None:
        if not isinstance(config.cycles, int) or isinstance(config.cycles, bool):
            raise ConfigurationError(
                f"cycles must be an int in 1..{MAX_SESSION_CYCLES}; "
                f"got {config.cycles!r}"
            )
        if config.cycles < 1 or config.cycles > MAX_SESSION_CYCLES:
            raise ConfigurationError(
                f"cycles must be in 1..{MAX_SESSION_CYCLES}; got {config.cycles}"
            )
        if not isinstance(config.symbol, str) or not config.symbol.strip():
            raise ConfigurationError("symbol must be a non-empty string")
        if config.bar_limit <= 0:
            raise ConfigurationError("bar_limit must be a positive integer")

    def _portfolio(self) -> Any:
        portfolio = getattr(self._runtime, "portfolio", None)
        if portfolio is None:
            raise ConfigurationError(
                "runtime has no portfolio; SessionRunner requires a shared portfolio"
            )
        return portfolio

    @staticmethod
    def _equity(portfolio: Any) -> Decimal:
        total = getattr(portfolio, "total_value", None)
        if total is None:
            raise ConfigurationError(
                "portfolio has no total_value; cannot compute session equity"
            )
        return Decimal(total) if not isinstance(total, Decimal) else total
