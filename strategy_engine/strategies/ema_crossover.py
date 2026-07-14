"""EMA crossover strategy using technical_analysis indicators."""

from __future__ import annotations

from decimal import Decimal

from core.types import MarketBar, SignalAction
from strategy_engine.base import BaseStrategy
from strategy_engine.signal import StrategySignal
from technical_analysis.calculator import IndicatorCalculator
from technical_analysis.registry import build_result_key
from technical_analysis.types import IndicatorRequest, IndicatorSource


class EmaCrossoverStrategy(BaseStrategy):
    """Generates BUY/SELL signals when a fast EMA crosses a slow EMA."""

    def __init__(self, fast_period: int = 12, slow_period: int = 26) -> None:
        if fast_period <= 0 or slow_period <= 0:
            raise ValueError("EMA periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")

        self._fast_period = fast_period
        self._slow_period = slow_period
        self._fast_key = build_result_key("ema", {"period": fast_period})
        self._slow_key = build_result_key("ema", {"period": slow_period})

    @property
    def name(self) -> str:
        return "ema_crossover"

    @property
    def fast_period(self) -> int:
        return self._fast_period

    @property
    def slow_period(self) -> int:
        return self._slow_period

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
                    name="ema",
                    params={"period": self._fast_period},
                    source=IndicatorSource.CLOSE,
                ),
                IndicatorRequest(
                    name="ema",
                    params={"period": self._slow_period},
                    source=IndicatorSource.CLOSE,
                ),
            ],
            symbol=symbol,
        )

        fast_series = snapshot.series.get(self._fast_key, [])
        slow_series = snapshot.series.get(self._slow_key, [])
        price = bars[-1].close
        crossover = _detect_crossover(fast_series, slow_series)

        fast_ema = snapshot.values.get(self._fast_key)
        slow_ema = snapshot.values.get(self._slow_key)
        metadata: dict[str, str | float] = {
            "fast_period": self._fast_period,
            "slow_period": self._slow_period,
        }
        if fast_ema is not None:
            metadata["fast_ema"] = float(fast_ema)
        if slow_ema is not None:
            metadata["slow_ema"] = float(slow_ema)

        if crossover is None:
            metadata["note"] = "Insufficient EMA history for crossover detection"
            return self._signal(
                symbol=symbol,
                action=SignalAction.HOLD,
                confidence=0.0,
                price=price,
                metadata=metadata,
            )

        action, confidence = crossover
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


def _detect_crossover(
    fast_series: list[Decimal | None],
    slow_series: list[Decimal | None],
) -> tuple[SignalAction, float] | None:
    pairs: list[tuple[Decimal, Decimal]] = []
    for fast_value, slow_value in zip(fast_series, slow_series, strict=False):
        if fast_value is not None and slow_value is not None:
            pairs.append((fast_value, slow_value))

    if len(pairs) < 2:
        return None

    prev_fast, prev_slow = pairs[-2]
    curr_fast, curr_slow = pairs[-1]
    spread = abs(curr_fast - curr_slow)
    reference = abs(curr_slow) if curr_slow != 0 else Decimal("1")
    strength = float(spread / reference)
    confidence = round(min(0.95, 0.55 + strength), 2)

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return SignalAction.BUY, confidence

    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return SignalAction.SELL, confidence

    return SignalAction.HOLD, 0.5
