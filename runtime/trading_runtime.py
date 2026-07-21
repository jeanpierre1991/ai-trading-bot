"""Basic trading runtime (Milestone 5).

Coordinates one decision cycle: market data → strategy → risk gate →
optional TradeIntent → portfolio snapshot. Does not place orders or mutate
portfolio state.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from config.settings import Settings
from core.types import OrderType, Side, SignalAction, Symbol
from risk_manager.base import RiskManager
from risk_manager.models import RiskEvaluation
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.models import PipelineResult, TradeIntent
from runtime.risk_gate import apply_risk_gate
from strategy_engine.signal import StrategySignal

_QTY = Decimal("0.0001")


class BasicTradingRuntime(TradingRuntime):
    """Minimal runtime for paper / live / backtest decision cycles.

    Dependencies are injected so adapters can swap market data, strategy
    evaluation, risk, and portfolio implementations without changing this
    contract. Order execution remains out of scope.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        market_data: Any,
        strategy_engine: Any,
        risk_manager: RiskManager,
        portfolio: Any,
    ) -> None:
        self._settings = settings
        self._market_data = market_data
        self._strategy_engine = strategy_engine
        self._risk_manager = risk_manager
        self._portfolio = portfolio

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

    def run_once(self, context: RuntimeContext) -> PipelineResult:
        """Run one coordinated cycle and return a PipelineResult.

        Expected validation failures become controlled PipelineResult values.
        Unexpected errors are not swallowed.
        """
        try:
            bars = self._market_data.get_bars(
                symbol=context.symbol,
                limit=context.bar_limit,
            )
        except ValueError as exc:
            return PipelineResult(
                success=False,
                stage_reached="market_data",
                aborted_reason=str(exc),
                portfolio_snapshot=self._portfolio_snapshot(),
            )

        if not bars:
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
        )
        snapshot = self._portfolio_snapshot()

        if signal.action is SignalAction.HOLD:
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
            return PipelineResult(
                success=False,
                stage_reached="risk",
                aborted_reason=gate.aborted_reason,
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=None,
                portfolio_snapshot=snapshot,
            )

        intent = self._build_trade_intent(signal, gate.evaluation)
        if intent is None:
            return PipelineResult(
                success=False,
                stage_reached="intent",
                aborted_reason="Computed trade quantity must be positive",
                signal=signal,
                risk_evaluation=gate.evaluation,
                intent=None,
                portfolio_snapshot=snapshot,
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

    def _resolve_portfolio_value(self, context: RuntimeContext) -> Decimal:
        if context.portfolio_value is not None:
            return Decimal(str(context.portfolio_value))
        return Decimal(str(self._portfolio.total_value))

    def _portfolio_snapshot(self) -> dict[str, float | int]:
        return dict(self._portfolio.summary())

    @staticmethod
    def _build_trade_intent(
        signal: StrategySignal,
        evaluation: RiskEvaluation,
    ) -> TradeIntent | None:
        side = _side_from_action(signal.action)
        max_position_value = evaluation.position_size
        quantity = (max_position_value / signal.price).quantize(_QTY, rounding=ROUND_DOWN)
        if quantity <= 0:
            return None

        return TradeIntent(
            symbol=Symbol(signal.symbol),
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            limit_price=None,
            strategy_name=signal.strategy_name,
            signal_confidence=signal.confidence,
            max_position_value=max_position_value,
            reason=evaluation.reason,
        )


def _side_from_action(action: SignalAction) -> Side:
    if action is SignalAction.BUY:
        return Side.BUY
    if action in (SignalAction.SELL, SignalAction.CLOSE):
        return Side.SELL
    raise ValueError(f"Cannot map signal action '{action}' to order side")
