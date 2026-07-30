"""Bounded paper operator skeleton (Milestone 12.1).

Composes existing ``TradingRuntime.run_once`` with hard bounds and a kill
switch. Does not sleep between cycles (M12.3), persist state (M12.2), or
expose a CLI (M12.3). Leaves ``SessionRunner`` semantics unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Literal

from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import TradingMode
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.kill_switch import KillSwitch
from runtime.models import PipelineResult
from runtime.session import compute_session_pnl_pct

# Hard ceiling so operator configs cannot become unbounded loops.
MAX_OPERATOR_CYCLES = 10_000

# Decision A2: only these unsuccessful stages may continue the operator loop.
# Do not broaden to generic success=False handling.
_CONTINUE_ON_STAGES = frozenset({"market_hours"})

ExecutionBackend = Literal["dry_run", "paper"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PaperOperatorConfig:
    """Hard-bounded inputs for one paper-operator run (M12.1).

    ``interval_seconds`` is validated now; sleeping is deferred to M12.3.
    Persistence fields are deferred to M12.2.
    """

    symbol: str
    max_cycles: int
    max_wall_time_seconds: float
    interval_seconds: float
    strategy_name: str | None = None
    bar_limit: int = 100
    execution: ExecutionBackend = "dry_run"


@dataclass(frozen=True)
class PaperOperatorResult:
    """Aggregated outcome of a ``PaperOperator.run`` call."""

    cycles_requested: int
    cycles_executed: int
    results: tuple[PipelineResult, ...]
    operator_start_equity: Decimal
    operator_end_equity: Decimal
    stopped_early: bool
    stop_reason: str | None
    kill_engaged: bool
    bound_reached: str | None

    @property
    def completed_all_cycles(self) -> bool:
        return (
            not self.stopped_early
            and self.cycles_executed == self.cycles_requested
        )


class PaperOperator:
    """Orchestrates hard-bounded paper/dry-run cycles on one TradingRuntime.

    Safety (approved M12 decisions):
    - Kill switch checked before every cycle, including the first.
    - Bounds required: max_cycles, max_wall_time_seconds, interval_seconds.
    - Unattended hours profile (B1): refuse start unless hours enabled + reject.
    - Abort classification (A2): continue only on ``stage_reached=market_hours``;
      all other unsuccessful cycles hard-stop.
    """

    def __init__(
        self,
        runtime: TradingRuntime,
        *,
        settings: Settings,
        kill_switch: KillSwitch,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._settings = settings
        self._kill_switch = kill_switch
        self._clock = clock if clock is not None else utc_now

    @property
    def runtime(self) -> TradingRuntime:
        return self._runtime

    def run(self, config: PaperOperatorConfig) -> PaperOperatorResult:
        self._validate_config(config)
        self._validate_unattended_hours_profile()
        self._validate_paper_settings_mode()

        if self._kill_switch.is_engaged():
            raise ConfigurationError(
                "paper operator kill switch is engaged; refusing to start"
            )

        portfolio = self._portfolio()
        operator_start_equity = self._equity(portfolio)
        if operator_start_equity <= 0:
            raise ConfigurationError(
                "operator_start_equity must be positive; "
                f"got {operator_start_equity}"
            )

        started_at = self._clock()
        results: list[PipelineResult] = []
        stopped_early = False
        stop_reason: str | None = None
        kill_engaged = False
        bound_reached: str | None = None

        for index in range(config.max_cycles):
            if self._kill_switch.is_engaged():
                stopped_early = True
                kill_engaged = True
                stop_reason = "kill_switch_engaged"
                break

            elapsed = (self._clock() - started_at).total_seconds()
            if elapsed >= config.max_wall_time_seconds:
                stopped_early = True
                bound_reached = "max_wall_time"
                stop_reason = (
                    f"max_wall_time_seconds reached ({config.max_wall_time_seconds})"
                )
                break

            current_equity = self._equity(portfolio)
            daily_pnl_pct = compute_session_pnl_pct(
                operator_start_equity,
                current_equity,
            )
            context = RuntimeContext(
                symbol=config.symbol,
                mode=TradingMode.PAPER,
                strategy_name=config.strategy_name,
                bar_limit=config.bar_limit,
                daily_pnl_pct=daily_pnl_pct,
            )
            result = self._runtime.run_once(context)
            results.append(result)

            if result.success:
                continue

            if self._is_continue_on(result):
                # Decision A2: expected non-actionable market_hours rejection.
                continue

            stopped_early = True
            stop_reason = (
                result.aborted_reason
                or f"cycle {index} aborted at stage {result.stage_reached!r}"
            )
            break
        else:
            # Completed configured cycles without early break from kill/wall/hard-stop.
            # max_cycles itself is a bound; surface it when the loop finishes normally.
            if len(results) >= config.max_cycles:
                bound_reached = "max_cycles"

        return PaperOperatorResult(
            cycles_requested=config.max_cycles,
            cycles_executed=len(results),
            results=tuple(results),
            operator_start_equity=operator_start_equity,
            operator_end_equity=self._equity(portfolio),
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            kill_engaged=kill_engaged,
            bound_reached=bound_reached,
        )

    @staticmethod
    def _is_continue_on(result: PipelineResult) -> bool:
        return result.stage_reached in _CONTINUE_ON_STAGES

    def _validate_config(self, config: PaperOperatorConfig) -> None:
        if not isinstance(config.max_cycles, int) or isinstance(
            config.max_cycles, bool
        ):
            raise ConfigurationError(
                f"max_cycles must be an int in 1..{MAX_OPERATOR_CYCLES}; "
                f"got {config.max_cycles!r}"
            )
        if config.max_cycles < 1 or config.max_cycles > MAX_OPERATOR_CYCLES:
            raise ConfigurationError(
                f"max_cycles must be in 1..{MAX_OPERATOR_CYCLES}; "
                f"got {config.max_cycles}"
            )

        if not isinstance(config.max_wall_time_seconds, (int, float)) or isinstance(
            config.max_wall_time_seconds, bool
        ):
            raise ConfigurationError(
                "max_wall_time_seconds must be a positive finite number; "
                f"got {config.max_wall_time_seconds!r}"
            )
        wall = float(config.max_wall_time_seconds)
        if not math.isfinite(wall) or wall <= 0:
            raise ConfigurationError(
                "max_wall_time_seconds must be a positive finite number; "
                f"got {config.max_wall_time_seconds!r}"
            )

        if not isinstance(config.interval_seconds, (int, float)) or isinstance(
            config.interval_seconds, bool
        ):
            raise ConfigurationError(
                "interval_seconds must be a positive finite number; "
                f"got {config.interval_seconds!r}"
            )
        interval = float(config.interval_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ConfigurationError(
                "interval_seconds must be a positive finite number; "
                f"got {config.interval_seconds!r}"
            )

        if not isinstance(config.symbol, str) or not config.symbol.strip():
            raise ConfigurationError("symbol must be a non-empty string")
        if config.bar_limit <= 0:
            raise ConfigurationError("bar_limit must be a positive integer")
        if config.execution not in ("dry_run", "paper"):
            raise ConfigurationError(
                f"execution must be 'dry_run' or 'paper'; got {config.execution!r}"
            )

    def _validate_unattended_hours_profile(self) -> None:
        """Decision B1: refuse start unless hours enabled + reject."""
        if not bool(self._settings.market_hours_enabled):
            raise ConfigurationError(
                "paper operator requires MARKET_HOURS_ENABLED=true; "
                "refusing to start"
            )
        policy = str(self._settings.market_hours_policy).strip().lower()
        if policy != "reject":
            raise ConfigurationError(
                "paper operator requires MARKET_HOURS_POLICY=reject; "
                f"got {self._settings.market_hours_policy!r}; refusing to start"
            )

    def _validate_paper_settings_mode(self) -> None:
        if self._settings.trading_mode != "paper":
            raise ConfigurationError(
                "paper operator requires trading_mode='paper'; "
                f"got {self._settings.trading_mode!r}; refusing to start"
            )

    def _portfolio(self) -> Any:
        portfolio = getattr(self._runtime, "portfolio", None)
        if portfolio is None:
            raise ConfigurationError(
                "runtime has no portfolio; PaperOperator requires a shared portfolio"
            )
        return portfolio

    @staticmethod
    def _equity(portfolio: Any) -> Decimal:
        total = getattr(portfolio, "total_value", None)
        if total is None:
            raise ConfigurationError(
                "portfolio has no total_value; cannot compute operator equity"
            )
        return Decimal(total) if not isinstance(total, Decimal) else total
