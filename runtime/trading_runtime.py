"""Basic trading runtime (Milestone 5–8).

Coordinates one decision cycle:
market data → strategy → risk gate → optional TradeIntent → optional
OrderExecutor → optional portfolio booking → portfolio snapshot.

Bookable ``ExecutionResult`` values (typically ``FILLED``) are mapped with
``execution_to_fill`` and applied via ``Portfolio.apply_fill``. Rejected or
non-bookable executions do not mutate the portfolio.

M8 mode policy: only paper / dry-run execution is allowed. ``trading_mode=live``,
backtest, and non-paper broker executors are rejected before the cycle runs.

M8.3 observability: structured cycle logs via ``trading_bot.runtime`` (no
behavioral changes).

Supported executor wiring (inject one or none):
- ``None`` — intent-only cycle (stage ``portfolio``, no booking)
- ``DryRunExecutor`` — simulated fill, then booking when bookable
- ``BrokerOrderExecutor(PaperBroker)`` — paper ``place_order``, then booking
"""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from typing import Any

from broker_interface.execution import ExecutionStatus
from config.settings import Settings
from core.types import OrderType, Side, SignalAction, Symbol
from portfolio_manager.portfolio import Fill
from risk_manager.base import RiskManager
from risk_manager.models import RiskEvaluation
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.executor import OrderExecutor
from runtime.fills import execution_to_fill, is_bookable
from runtime.mode_policy import mode_policy_violation
from runtime.models import PipelineResult, TradeIntent
from runtime.risk_gate import apply_risk_gate
from strategy_engine.signal import StrategySignal

_QTY = Decimal("0.0001")
_MONEY = Decimal("0.01")
_logger = logging.getLogger("trading_bot.runtime")


def _log_runtime_event(
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured runtime cycle log line (observability only)."""
    payload = " ".join(f"{key}={value}" for key, value in fields.items())
    _logger.log(level, "%s %s", event, payload)


class BasicTradingRuntime(TradingRuntime):
    """Minimal runtime for paper / dry-run decision cycles (M8).

    Dependencies are injected so adapters can swap market data, strategy
    evaluation, risk, portfolio, and optional order execution without changing
    this contract. After a bookable fill, the portfolio is updated through
    ``apply_fill`` and ``stage_reached`` becomes ``\"portfolio\"``.

    Allowed executors: ``None``, ``DryRunExecutor``, or
    ``BrokerOrderExecutor(PaperBroker)``. Live mode and non-paper brokers are
    rejected by the mode policy before market data is fetched.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        market_data: Any,
        strategy_engine: Any,
        risk_manager: RiskManager,
        portfolio: Any,
        executor: OrderExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._market_data = market_data
        self._strategy_engine = strategy_engine
        self._risk_manager = risk_manager
        self._portfolio = portfolio
        self._executor = executor

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def market_data(self) -> Any:
        return self._market_data

    @property
    def strategy_engine(self) -> Any:
        return self._strategy_engine

    @property
    def risk_manager(self) -> RiskManager:
        return self._risk_manager

    @property
    def portfolio(self) -> Any:
        return self._portfolio

    @property
    def executor(self) -> OrderExecutor | None:
        return self._executor

    def run_once(self, context: RuntimeContext) -> PipelineResult:
        """Run one coordinated cycle and return a PipelineResult.

        Expected validation failures and booking ``ValueError`` values become
        controlled ``PipelineResult`` outcomes. Unexpected errors are not
        swallowed. Bookable fills update the portfolio before the final
        snapshot when booking succeeds. Mode-policy violations abort at stage
        ``\"mode\"`` before any market-data call.
        """
        executor_name = (
            type(self._executor).__name__ if self._executor is not None else "None"
        )
        mode_value = getattr(context.mode, "value", context.mode)
        _log_runtime_event(
            "cycle_start",
            symbol=context.symbol,
            mode=mode_value,
            executor=executor_name,
        )

        mode_abort = mode_policy_violation(
            settings_trading_mode=self._settings.trading_mode,
            context_mode=context.mode,
            executor=self._executor,
        )
        if mode_abort is not None:
            _log_runtime_event(
                "mode_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="mode",
                reason=mode_abort,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="mode",
            )
            return PipelineResult(
                success=False,
                stage_reached="mode",
                aborted_reason=mode_abort,
                portfolio_snapshot=self._portfolio_snapshot(),
            )

        try:
            bars = self._market_data.get_bars(
                symbol=context.symbol,
                limit=context.bar_limit,
            )
        except ValueError as exc:
            _log_runtime_event(
                "market_data_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="market_data",
                reason=str(exc),
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="market_data",
            )
            return PipelineResult(
                success=False,
                stage_reached="market_data",
                aborted_reason=str(exc),
                portfolio_snapshot=self._portfolio_snapshot(),
            )

        if not bars:
            _log_runtime_event(
                "market_data_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="market_data",
                reason="No market bars available",
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="market_data",
            )
            return PipelineResult(
                success=False,
                stage_reached="market_data",
                aborted_reason="No market bars available",
                portfolio_snapshot=self._portfolio_snapshot(),
            )

        try:
            signal = self._strategy_engine.evaluate(
                bars,
                symbol=context.symbol,
                strategy_name=context.strategy_name,
            )
        except ValueError as exc:
            _log_runtime_event(
                "strategy_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="strategy",
                reason=str(exc),
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="strategy",
            )
            return PipelineResult(
                success=False,
                stage_reached="strategy",
                aborted_reason=str(exc),
                portfolio_snapshot=self._portfolio_snapshot(),
            )

        portfolio_value = self._resolve_portfolio_value(context)
        gate = apply_risk_gate(
            signal,
            portfolio_value=portfolio_value,
            risk_manager=self._risk_manager,
            open_positions=self._resolve_open_positions(),
            daily_pnl_pct=context.daily_pnl_pct,
            opens_new_exposure=self._would_open_new_exposure(signal),
        )
        snapshot = self._portfolio_snapshot()

        if signal.action is SignalAction.HOLD:
            _log_runtime_event(
                "hold_success",
                symbol=context.symbol,
                stage="risk",
                action=signal.action.value,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=True,
                stage="risk",
            )
            return PipelineResult(
                success=True,
                stage_reached="risk",
                aborted_reason=None,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=None,
                portfolio_snapshot=snapshot,
            )

        if not gate.approved:
            _log_runtime_event(
                "risk_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="risk",
                reason=gate.aborted_reason,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="risk",
            )
            return PipelineResult(
                success=False,
                stage_reached="risk",
                aborted_reason=gate.aborted_reason,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=None,
                portfolio_snapshot=snapshot,
            )

        intent, abort_reason = self._try_build_trade_intent(signal, gate.evaluation)
        if abort_reason is not None:
            _log_runtime_event(
                "intent_abort",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="intent",
                reason=abort_reason,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="intent",
            )
            return PipelineResult(
                success=False,
                stage_reached="intent",
                aborted_reason=abort_reason,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=None,
                portfolio_snapshot=snapshot,
            )

        if self._executor is None:
            _log_runtime_event(
                "intent_only_success",
                symbol=context.symbol,
                stage="portfolio",
                action=signal.action.value,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=True,
                stage="portfolio",
            )
            return PipelineResult(
                success=True,
                stage_reached="portfolio",
                aborted_reason=None,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=intent,
                portfolio_snapshot=snapshot,
            )

        execution = self._executor.execute(intent)
        _log_runtime_event(
            "execution_result",
            symbol=context.symbol,
            status=execution.status.value,
        )
        if is_bookable(execution):
            try:
                fill = execution_to_fill(execution)
            except ValueError as exc:
                reason = f"execution_to_fill failed: {exc}"
                _log_runtime_event(
                    "booking_failure",
                    level=logging.WARNING,
                    symbol=context.symbol,
                    stage="execution",
                    reason=reason,
                )
                _log_runtime_event(
                    "cycle_end",
                    symbol=context.symbol,
                    success=False,
                    stage="execution",
                )
                return PipelineResult(
                    success=False,
                    stage_reached="execution",
                    aborted_reason=reason,
                    signal=signal,
                    risk_evaluation=gate.evaluation,
                    intent=intent,
                    execution=execution,
                    portfolio_snapshot=self._portfolio_snapshot(),
                )

            if fill is None:
                reason = (
                    "bookable execution produced no fill "
                    "(execution_to_fill contract violation)"
                )
                _log_runtime_event(
                    "booking_failure",
                    level=logging.WARNING,
                    symbol=context.symbol,
                    stage="execution",
                    reason=reason,
                )
                _log_runtime_event(
                    "cycle_end",
                    symbol=context.symbol,
                    success=False,
                    stage="execution",
                )
                return PipelineResult(
                    success=False,
                    stage_reached="execution",
                    aborted_reason=reason,
                    signal=signal,
                    risk_evaluation=gate.evaluation,
                    intent=intent,
                    execution=execution,
                    portfolio_snapshot=self._portfolio_snapshot(),
                )

            try:
                self._book_execution(fill)
            except ValueError as exc:
                reason = f"apply_fill failed: {exc}"
                _log_runtime_event(
                    "booking_failure",
                    level=logging.WARNING,
                    symbol=context.symbol,
                    stage="portfolio",
                    reason=reason,
                )
                _log_runtime_event(
                    "cycle_end",
                    symbol=context.symbol,
                    success=False,
                    stage="portfolio",
                )
                return PipelineResult(
                    success=False,
                    stage_reached="portfolio",
                    aborted_reason=reason,
                    signal=signal,
                    risk_evaluation=gate.evaluation,
                    intent=intent,
                    execution=execution,
                    portfolio_snapshot=self._portfolio_snapshot(),
                )
            snapshot = self._portfolio_snapshot()
            _log_runtime_event(
                "booking_success",
                symbol=context.symbol,
                stage="portfolio",
                status=execution.status.value,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=True,
                stage="portfolio",
            )
            return PipelineResult(
                success=True,
                stage_reached="portfolio",
                aborted_reason=None,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=intent,
                execution=execution,
                portfolio_snapshot=snapshot,
            )

        if execution.status is ExecutionStatus.REJECTED:
            _log_runtime_event(
                "execution_rejected",
                level=logging.WARNING,
                symbol=context.symbol,
                stage="execution",
                reason=execution.message,
            )
            _log_runtime_event(
                "cycle_end",
                symbol=context.symbol,
                success=False,
                stage="execution",
            )
            return PipelineResult(
                success=False,
                stage_reached="execution",
                aborted_reason=execution.message,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=intent,
                execution=execution,
                portfolio_snapshot=snapshot,
            )

        _log_runtime_event(
            "cycle_end",
            symbol=context.symbol,
            success=True,
            stage="execution",
        )
        return PipelineResult(
            success=True,
            stage_reached="execution",
            aborted_reason=None,
            signal=signal,
            risk_evaluation=gate.evaluation,
            intent=intent,
            execution=execution,
            portfolio_snapshot=snapshot,
        )

    def _resolve_portfolio_value(self, context: RuntimeContext) -> Decimal:
        """Prefer real Portfolio.total_value; context value is fallback only."""
        if self._portfolio_is_usable():
            return Decimal(str(self._portfolio.total_value))
        if context.portfolio_value is not None:
            return Decimal(str(context.portfolio_value))
        return Decimal("0")

    def _resolve_open_positions(self) -> int:
        """Return current open position count from the injected portfolio."""
        portfolio = self._portfolio
        position_count = getattr(portfolio, "position_count", None)
        if isinstance(position_count, int):
            return position_count
        if callable(position_count):
            return int(position_count())
        positions = getattr(portfolio, "positions", None)
        if isinstance(positions, dict):
            return len(positions)
        return 0

    def _would_open_new_exposure(self, signal: StrategySignal) -> bool:
        """True when the signal would add a new position slot (not add-to/reduce)."""
        if signal.action is not SignalAction.BUY:
            return False
        positions = getattr(self._portfolio, "positions", None)
        if isinstance(positions, dict) and str(signal.symbol) in positions:
            return False
        return True

    def _portfolio_is_usable(self) -> bool:
        portfolio = self._portfolio
        return hasattr(portfolio, "total_value") and hasattr(portfolio, "summary")

    def _portfolio_snapshot(self) -> dict[str, float | int] | None:
        summary = getattr(self._portfolio, "summary", None)
        if not callable(summary):
            return None
        return dict(summary())

    def _book_execution(self, fill: Fill) -> Fill:
        """Apply ``fill`` to the portfolio.

        Does not map ExecutionResult, build PipelineResult, or take snapshots.
        Propagates ``apply_fill`` errors without catching them.
        """
        self._portfolio.apply_fill(fill)
        return fill

    def _long_quantity(self, symbol: str) -> Decimal:
        positions = getattr(self._portfolio, "positions", None)
        if not isinstance(positions, dict):
            return Decimal("0")

        position = positions.get(str(symbol))
        if position is None:
            return Decimal("0")

        if getattr(position, "side", None) is not Side.BUY:
            return Decimal("0")

        quantity = getattr(position, "quantity", None)
        if quantity is None:
            return Decimal("0")
        return Decimal(str(quantity))

    def _try_build_trade_intent(
        self,
        signal: StrategySignal,
        evaluation: RiskEvaluation,
    ) -> tuple[TradeIntent | None, str | None]:
        if signal.action is SignalAction.BUY:
            intent = self._build_buy_intent(signal, evaluation)
            if intent is None:
                return None, "Computed trade quantity must be positive"
            return intent, None

        if signal.action in (SignalAction.SELL, SignalAction.CLOSE):
            return self._build_exit_intent(signal, evaluation)

        return None, f"Unsupported signal action for trade intent: {signal.action}"

    def _build_buy_intent(
        self,
        signal: StrategySignal,
        evaluation: RiskEvaluation,
    ) -> TradeIntent | None:
        max_position_value = evaluation.position_size
        quantity = (max_position_value / signal.price).quantize(_QTY, rounding=ROUND_DOWN)
        if quantity <= 0:
            return None

        return TradeIntent(
            symbol=Symbol(signal.symbol),
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
            limit_price=None,
            strategy_name=signal.strategy_name,
            signal_confidence=signal.confidence,
            max_position_value=max_position_value,
            reason=evaluation.reason,
        )

    def _build_exit_intent(
        self,
        signal: StrategySignal,
        evaluation: RiskEvaluation,
    ) -> tuple[TradeIntent | None, str | None]:
        available = self._long_quantity(signal.symbol)
        if available <= 0:
            return None, f"No open long position for {signal.symbol}"

        if signal.action is SignalAction.CLOSE:
            quantity = available
            max_position_value = (quantity * signal.price).quantize(_MONEY)
        else:
            sized = (evaluation.position_size / signal.price).quantize(
                _QTY, rounding=ROUND_DOWN
            )
            if sized <= 0:
                return None, "Computed trade quantity must be positive"
            if sized > available:
                return (
                    None,
                    (
                        f"Sell quantity {sized} exceeds available "
                        f"{available} for {signal.symbol}"
                    ),
                )
            quantity = sized
            max_position_value = evaluation.position_size

        return (
            TradeIntent(
                symbol=Symbol(signal.symbol),
                side=Side.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                limit_price=None,
                strategy_name=signal.strategy_name,
                signal_confidence=signal.confidence,
                max_position_value=max_position_value,
                reason=evaluation.reason,
            ),
            None,
        )
