"""Bounded paper operator with durable resume (Milestones 12.1–12.4).

Composes existing ``TradingRuntime.run_once`` with hard bounds, a kill switch,
atomic JSON state persistence, injectable interval sleeping, and start-time
state-path writability preflight (M12.4 S1b). CLI wiring lives in ``main.py``
(``run-paper-operator``). Leaves ``SessionRunner`` semantics unchanged.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from config.settings import Settings
from core.exceptions import ConfigurationError
from core.types import TradingMode
from order_manager.manager import OrderManager
from portfolio_manager.portfolio import Portfolio
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.kill_switch import KillSwitch
from runtime.models import PipelineResult
from runtime.paper_state import (
    OPERATOR_STATE_SCHEMA_VERSION,
    OperatorState,
    apply_order_manager_snapshot,
    apply_portfolio_snapshot,
    normalize_bar_timestamp,
    order_manager_to_snapshot,
    portfolio_to_snapshot,
)
from runtime.paper_state_store import JsonPaperStateStore, PaperStateStore
from runtime.session import compute_session_pnl_pct

# Hard ceiling so operator configs cannot become unbounded loops.
MAX_OPERATOR_CYCLES = 10_000

# Decision A2: only these unsuccessful stages may continue the operator loop.
# Do not broaden to generic success=False handling.
_CONTINUE_ON_STAGES = frozenset({"market_hours"})

ExecutionBackend = Literal["dry_run", "paper"]
Sleeper = Callable[[float], None]

_logger = logging.getLogger("trading_bot.paper_operator")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PaperOperatorConfig:
    """Hard-bounded inputs for one paper-operator run.

    ``interval_seconds`` is the sleep duration between cycles (M12.3).
    ``state_path`` enables durable load/save when set (required by CLI).
    """

    symbol: str
    max_cycles: int
    max_wall_time_seconds: float
    interval_seconds: float
    strategy_name: str | None = None
    bar_limit: int = 100
    execution: ExecutionBackend = "dry_run"
    state_path: Path | None = None


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
    cycles_completed_total: int = 0
    resumed: bool = False

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
    - Abort classification (A2): continue only on ``stage_reached=market_hours``.
    - M12.2: atomic JSON persistence, D1 equity baseline, E1 actionable-bar cursor.
    """

    def __init__(
        self,
        runtime: TradingRuntime,
        *,
        settings: Settings,
        kill_switch: KillSwitch,
        clock: Callable[[], datetime] | None = None,
        state_store: PaperStateStore | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._runtime = runtime
        self._settings = settings
        self._kill_switch = kill_switch
        self._clock = clock if clock is not None else utc_now
        self._state_store = state_store
        self._sleeper: Sleeper = time.sleep if sleeper is None else sleeper

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

        store = self._resolve_store(config)
        # M12.4 S1(b): fail closed on state-path FS issues before any run_once.
        if store is not None:
            store.ensure_parent_writable()

        portfolio = self._portfolio()
        order_manager = self._order_manager()

        resumed = False
        cycles_completed_total = 0
        last_actionable_bar_timestamps: dict[str, str] = {}
        last_cycle_at: datetime | None = None

        if store is not None and store.exists():
            loaded = store.load()
            self._restore_runtime_state(portfolio, order_manager, loaded)
            operator_start_equity = loaded.operator_start_equity
            cycles_completed_total = loaded.cycles_completed_total
            last_actionable_bar_timestamps = dict(
                loaded.last_actionable_bar_timestamps
            )
            last_cycle_at = loaded.last_cycle_at
            resumed = True
        else:
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

        def persist(*, touch_cycle_at: bool) -> None:
            if store is None:
                return
            cycle_at = self._clock() if touch_cycle_at else last_cycle_at
            store.save(
                OperatorState(
                    schema_version=OPERATOR_STATE_SCHEMA_VERSION,
                    portfolio=portfolio_to_snapshot(portfolio),
                    order_manager=order_manager_to_snapshot(order_manager),
                    cycles_completed_total=cycles_completed_total,
                    operator_start_equity=operator_start_equity,
                    last_cycle_at=cycle_at,
                    last_actionable_bar_timestamps=dict(
                        last_actionable_bar_timestamps
                    ),
                )
            )

        for index in range(config.max_cycles):
            if self._kill_switch.is_engaged():
                stopped_early = True
                kill_engaged = True
                stop_reason = "kill_switch_engaged"
                persist(touch_cycle_at=False)
                break

            elapsed = (self._clock() - started_at).total_seconds()
            if elapsed >= config.max_wall_time_seconds:
                stopped_early = True
                bound_reached = "max_wall_time"
                stop_reason = (
                    f"max_wall_time_seconds reached ({config.max_wall_time_seconds})"
                )
                persist(touch_cycle_at=False)
                break

            current_equity = self._equity(portfolio)
            daily_pnl_pct = compute_session_pnl_pct(
                operator_start_equity,
                current_equity,
            )
            # E1: pass persisted cursor only. The runtime compares it to the
            # authoritative bar timestamp from this cycle's market-data snapshot.
            already_actioned = last_actionable_bar_timestamps.get(config.symbol)

            context = RuntimeContext(
                symbol=config.symbol,
                mode=TradingMode.PAPER,
                strategy_name=config.strategy_name,
                bar_limit=config.bar_limit,
                daily_pnl_pct=daily_pnl_pct,
                already_actioned_bar_timestamp=already_actioned,
            )
            try:
                result = self._runtime.run_once(context)
            except Exception:
                # Do not persist a potentially inconsistent in-memory mutation.
                raise

            results.append(result)
            cycles_completed_total += 1
            last_cycle_at = self._clock()

            # Advance cursor only when this cycle produced actionable side effects
            # using the authoritative processed bar timestamp from PipelineResult.
            if (
                result.intent is not None or result.order is not None
            ) and result.market_bar_timestamp is not None:
                last_actionable_bar_timestamps[config.symbol] = (
                    normalize_bar_timestamp(result.market_bar_timestamp)
                )

            # Persist after each handled cycle before the next iteration.
            persist(touch_cycle_at=True)

            if result.success or self._is_continue_on(result):
                # M12.3: sleep between cycles when another cycle may still run.
                if index + 1 < config.max_cycles:
                    _logger.info(
                        "interval_sleep seconds=%s",
                        float(config.interval_seconds),
                    )
                    self._sleeper(float(config.interval_seconds))
                continue

            stopped_early = True
            stop_reason = (
                result.aborted_reason
                or f"cycle {index} aborted at stage {result.stage_reached!r}"
            )
            # State already saved after this cycle; hard-stop without further cycles.
            break
        else:
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
            cycles_completed_total=cycles_completed_total,
            resumed=resumed,
        )

    def _resolve_store(self, config: PaperOperatorConfig) -> PaperStateStore | None:
        if self._state_store is not None:
            return self._state_store
        if config.state_path is None:
            return None
        return JsonPaperStateStore(config.state_path)

    def _restore_runtime_state(
        self,
        portfolio: Any,
        order_manager: OrderManager | None,
        state: OperatorState,
    ) -> None:
        if not isinstance(portfolio, Portfolio):
            raise ConfigurationError(
                "paper operator resume requires a Portfolio instance on runtime"
            )
        try:
            apply_portfolio_snapshot(portfolio, state.portfolio)
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(
                f"failed to restore portfolio from operator state: {exc}"
            ) from exc

        if order_manager is None:
            if state.order_manager.get("orders"):
                raise ConfigurationError(
                    "operator state contains orders but runtime has no OrderManager"
                )
            return
        try:
            apply_order_manager_snapshot(order_manager, state.order_manager)
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(
                f"failed to restore OrderManager from operator state: {exc}"
            ) from exc

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
        if config.state_path is not None and not isinstance(config.state_path, Path):
            raise ConfigurationError(
                f"state_path must be a Path or None; got {config.state_path!r}"
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

    def _order_manager(self) -> OrderManager | None:
        order_manager = getattr(self._runtime, "order_manager", None)
        if order_manager is None:
            return None
        if not isinstance(order_manager, OrderManager):
            raise ConfigurationError(
                "runtime.order_manager must be an OrderManager when present"
            )
        return order_manager

    @staticmethod
    def _equity(portfolio: Any) -> Decimal:
        total = getattr(portfolio, "total_value", None)
        if total is None:
            raise ConfigurationError(
                "portfolio has no total_value; cannot compute operator equity"
            )
        return Decimal(total) if not isinstance(total, Decimal) else total
