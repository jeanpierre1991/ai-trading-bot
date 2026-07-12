"""Technical analysis module implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth
from core.types import MarketBar
from technical_analysis.calculator import IndicatorCalculator
from technical_analysis.indicators import calculate_ema, calculate_rsi, calculate_sma
from technical_analysis.types import IndicatorRequest, IndicatorSource

_DEFAULT_ANALYZE_REQUESTS: tuple[IndicatorRequest, ...] = (
    IndicatorRequest(name="sma", params={"period": 20}, source=IndicatorSource.CLOSE),
    IndicatorRequest(name="ema", params={"period": 20}, source=IndicatorSource.CLOSE),
    IndicatorRequest(name="rsi", params={"period": 14}, source=IndicatorSource.CLOSE),
)


class TechnicalAnalysisModule(BaseModule):
    """Computes technical indicators from price series."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._indicators_available = ("sma", "ema", "rsi")

    @property
    def name(self) -> str:
        return "technical_analysis"

    def analyze(self, closes: list[Decimal]) -> dict[str, list[Decimal | None]]:
        return self._analyze_with_calculator(closes)

    def _analyze_legacy(self, closes: list[Decimal]) -> dict[str, list[Decimal | None]]:
        return {
            "sma_20": calculate_sma(closes, 20),
            "ema_20": calculate_ema(closes, 20),
            "rsi_14": calculate_rsi(closes, 14),
        }

    def _analyze_with_calculator(self, closes: list[Decimal]) -> dict[str, list[Decimal | None]]:
        bars = self._bars_from_closes(closes)
        snapshot = IndicatorCalculator.compute(bars, list(_DEFAULT_ANALYZE_REQUESTS))
        return {
            "sma_20": snapshot.series["sma_20"],
            "ema_20": snapshot.series["ema_20"],
            "rsi_14": snapshot.series["rsi_14"],
        }

    @staticmethod
    def _bars_from_closes(closes: list[Decimal]) -> list[MarketBar]:
        base_timestamp = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        bars: list[MarketBar] = []

        for index, close in enumerate(closes):
            bars.append(
                MarketBar(
                    timestamp=base_timestamp + timedelta(minutes=index),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("0"),
                )
            )

        return bars

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
