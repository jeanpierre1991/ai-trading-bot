"""AI engine module implementation."""

from __future__ import annotations

from decimal import Decimal

from ai_engine.analyzer import AIAnalyzer, MockAIAnalyzer
from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth


class AIEngineModule(BaseModule):
    """Provides AI-driven market analysis and signal generation."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._analyzer: AIAnalyzer | None = None
        self._analysis_count = 0

    @property
    def name(self) -> str:
        return "ai_engine"

    def _on_initialize(self) -> None:
        provider = self._settings.ai_provider.lower()
        if provider == "mock":
            self._analyzer = MockAIAnalyzer()
        else:
            self._analyzer = MockAIAnalyzer()
            self.logger.warning("AI provider '%s' not implemented; using mock", provider)

    def analyze(self, symbol: str, price: Decimal, indicators: dict[str, float] | None = None):
        if self._analyzer is None:
            raise RuntimeError("AI analyzer not initialized")
        self._analysis_count += 1
        return self._analyzer.analyze(symbol, price, indicators or {"rsi": 50.0})

    def health_check(self) -> ModuleHealth:
        if self._analyzer is None:
            return self._unhealthy("Analyzer not initialized")

        try:
            result = self.analyze("AAPL", Decimal("190.00"), {"rsi": 28.0})
            return self._healthy(
                message="AI analyzer operational",
                provider=self._settings.ai_provider,
                model=self._settings.ai_model,
                sample_action=result.action.value,
                sample_confidence=result.confidence,
                analyses_performed=self._analysis_count,
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
