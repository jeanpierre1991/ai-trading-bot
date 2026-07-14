"""RSI threshold strategy using technical_analysis indicators."""

from __future__ import annotations

from decimal import Decimal

from core.types import MarketBar, SignalAction
from strategy_engine.base import BaseStrategy
from strategy_engine.signal import StrategySignal
from technical_analysis.calculator import IndicatorCalculator
from technical_analysis.registry import build_result_key
from technical_analysis.types import IndicatorRequest, IndicatorSource


class RSIStrategy(BaseStrategy):
    """Generates BUY/SELL signals from RSI oversold and overbought levels."""

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ) -> None:
        if period <= 0:
            raise ValueError("RSI period must be positive")
        if not 0.0 <= oversold < overbought <= 100.0:
            raise ValueError("oversold must be < overbought and both within 0..100")

        self._period = period
        self._oversold = oversold
        self._overbought = overbought
        self._result_key = build_result_key("rsi", {"period": period})

    @property
    def name(self) -> str:
        return "rsi"

    @property
    def period(self) -> int:
        return self._period

    @property
    def oversold(self) -> float:
        return self._oversold

    @property
    def overbought(self) -> float:
        return self._overbought

    def evaluate(self, bars: list[MarketBar], *, symbol: str) -> StrategySignal:
        if not bars:
            return self._signal(
                symbol=symbol,
                action=SignalAction.HOLD,
                confidence=0.0,
                price=Decimal("0"),
                metadata={"note": "No market bars provided"},
            )

        snapshot = IndicatorCalculator.compute(
            bars,
            [
                IndicatorRequest(
                    name="rsi",
                    params={"period": self._period},
                    source=IndicatorSource.CLOSE,
                ),
            ],
            symbol=symbol,
        )

        price = bars[-1].close
        rsi_value = snapshot.values.get(self._result_key)
        metadata: dict[str, str | float] = {
            "period": self._period,
            "oversold": self._oversold,
            "overbought": self._overbought,
        }

        if rsi_value is None:
            metadata["note"] = "Insufficient RSI history"
            return self._signal(
                symbol=symbol,
                action=SignalAction.HOLD,
                confidence=0.0,
                price=price,
                metadata=metadata,
            )

        rsi = float(rsi_value)
        metadata["rsi"] = rsi
        action, confidence = _signal_from_rsi(rsi, self._oversold, self._overbought)
        return self._signal(
            symbol=symbol,
            action=action,
            confidence=confidence,
            price=price,
            metadata=metadata,
        )

    def _signal(
        self,
        *,
        symbol: str,
        action: SignalAction,
        confidence: float,
        price: Decimal,
        metadata: dict[str, str | float],
    ) -> StrategySignal:
        return StrategySignal(
            symbol=symbol,
            action=action,
            confidence=confidence,
            strategy_name=self.name,
            price=price,
            metadata=metadata,
        )


def _signal_from_rsi(
    rsi: float,
    oversold: float,
    overbought: float,
) -> tuple[SignalAction, float]:
    if rsi < oversold:
        confidence = min(0.95, (oversold - rsi) / oversold + 0.5) if oversold else 0.95
        return SignalAction.BUY, round(confidence, 2)

    if rsi > overbought:
        upper_span = 100.0 - overbought
        confidence = (
            min(0.95, (rsi - overbought) / upper_span + 0.5) if upper_span else 0.95
        )
        return SignalAction.SELL, round(confidence, 2)

    return SignalAction.HOLD, 0.5
