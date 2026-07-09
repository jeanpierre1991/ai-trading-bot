"""AI analysis abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from core.types import SignalAction


@dataclass(frozen=True)
class AIAnalysisResult:
    symbol: str
    action: SignalAction
    confidence: float
    reasoning: str
    features: dict[str, float]


class AIAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self,
        symbol: str,
        price: Decimal,
        indicators: dict[str, float],
    ) -> AIAnalysisResult:
        ...


class MockAIAnalyzer(AIAnalyzer):
    """Rule-based mock analyzer for architecture verification."""

    def analyze(
        self,
        symbol: str,
        price: Decimal,
        indicators: dict[str, float],
    ) -> AIAnalysisResult:
        rsi = indicators.get("rsi", 50.0)

        if rsi < 30:
            action = SignalAction.BUY
            confidence = min(0.9, (30 - rsi) / 30 + 0.5)
            reasoning = f"RSI {rsi:.1f} indicates oversold conditions"
        elif rsi > 70:
            action = SignalAction.SELL
            confidence = min(0.9, (rsi - 70) / 30 + 0.5)
            reasoning = f"RSI {rsi:.1f} indicates overbought conditions"
        else:
            action = SignalAction.HOLD
            confidence = 0.5
            reasoning = f"RSI {rsi:.1f} within neutral range"

        return AIAnalysisResult(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            features={"price": float(price), "rsi": rsi},
        )
