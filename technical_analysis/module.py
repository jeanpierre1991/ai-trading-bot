"""Technical analysis module implementation."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from technical_analysis.indicators import calculate_ema, calculate_rsi, calculate_sma


class TechnicalAnalysisModule(BaseModule):
    """Computes technical indicators from price series."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._indicators_available = ("sma", "ema", "rsi")

    @property
    def name(self) -> str:
        return "technical_analysis"

    def analyze(self, closes: list[Decimal]) -> dict[str, list[Decimal | None]]:
        return {
            "sma_20": calculate_sma(closes, 20),
            "ema_20": calculate_ema(closes, 20),
            "rsi_14": calculate_rsi(closes, 14),
        }

    def health_check(self) -> ModuleHealth:
        sample = [Decimal(str(100 + i * 0.5)) for i in range(30)]
        try:
            results = self.analyze(sample)
            latest_rsi = next(v for v in reversed(results["rsi_14"]) if v is not None)
            return self._healthy(
                message="Indicators computed successfully",
                indicators=self._indicators_available,
                sample_rsi=str(latest_rsi),
            )
        except Exception as exc:
            return self._unhealthy(f"Indicator computation failed: {exc}")
