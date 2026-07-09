"""Portfolio manager module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from portfolio_manager.portfolio import Portfolio


class PortfolioManagerModule(BaseModule):
    """Tracks portfolio state, positions, and performance."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._portfolio: Portfolio | None = None

    @property
    def name(self) -> str:
        return "portfolio_manager"

    def _on_initialize(self) -> None:
        self._portfolio = Portfolio(cash=self._settings.backtest_initial_capital)

    @property
    def portfolio(self) -> Portfolio:
        if self._portfolio is None:
            raise RuntimeError("Portfolio not initialized")
        return self._portfolio

    def health_check(self) -> ModuleHealth:
        if self._portfolio is None:
            return self._unhealthy("Portfolio not initialized")

        try:
            summary = self._portfolio.summary()
            return self._healthy(
                message="Portfolio manager operational",
                initial_capital=str(self._settings.backtest_initial_capital),
                currency=self._settings.base_currency,
                **{k: v for k, v in summary.items()},
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
