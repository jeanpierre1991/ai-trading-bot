"""Basic trading runtime scaffold (Milestone 5 structure).

Holds collaborators for the decision path. Cycle logic and broker/order
execution are intentionally not implemented yet.
"""

from __future__ import annotations

from typing import Any

from config.settings import Settings
from risk_manager.base import RiskManager
from runtime.base import TradingRuntime
from runtime.context import RuntimeContext
from runtime.models import PipelineResult


class BasicTradingRuntime(TradingRuntime):
    """Minimal runtime skeleton for paper / live / backtest extension.

    Dependencies are injected so adapters can swap market data, strategy
    evaluation, risk, and portfolio implementations without changing this
    contract.
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
        raise NotImplementedError(
            "BasicTradingRuntime cycle not implemented yet "
            "(market data → strategy → risk gate → trade intent → portfolio)"
        )
