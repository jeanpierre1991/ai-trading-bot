"""Backtesting module implementation."""

from __future__ import annotations

from backtesting.engine import BacktestEngine
from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth


class BacktestingModule(BaseModule):
    """Provides backtesting infrastructure for strategy validation."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._engine: BacktestEngine | None = None

    @property
    def name(self) -> str:
        return "backtesting"

    def _on_initialize(self) -> None:
        self._engine = BacktestEngine(
            initial_capital=self._settings.backtest_initial_capital,
            commission_pct=self._settings.backtest_commission_pct,
        )

    @property
    def engine(self) -> BacktestEngine:
        if self._engine is None:
            raise RuntimeError("Backtest engine not initialized")
        return self._engine

    def health_check(self) -> ModuleHealth:
        if self._engine is None:
            return self._unhealthy("Backtest engine not initialized")

        try:
            result = self._engine.run_dry_check()
            return self._healthy(
                message="Backtest engine ready (awaiting strategy logic)",
                initial_capital=str(self._settings.backtest_initial_capital),
                commission_pct=str(self._settings.backtest_commission_pct),
                dry_run=result.to_dict(),
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
