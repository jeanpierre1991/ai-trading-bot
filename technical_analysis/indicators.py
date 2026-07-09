"""Technical indicator calculations."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence


def calculate_sma(prices: Sequence[Decimal], period: int) -> list[Decimal | None]:
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[Decimal | None] = [None] * len(prices)
    if len(prices) < period:
        return result

    window_sum = sum(prices[:period])
    result[period - 1] = (window_sum / Decimal(period)).quantize(Decimal("0.0001"))

    for i in range(period, len(prices)):
        window_sum += prices[i] - prices[i - period]
        result[i] = (window_sum / Decimal(period)).quantize(Decimal("0.0001"))

    return result


def calculate_ema(prices: Sequence[Decimal], period: int) -> list[Decimal | None]:
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[Decimal | None] = [None] * len(prices)
    if len(prices) < period:
        return result

    sma_seed = sum(prices[:period]) / Decimal(period)
    result[period - 1] = sma_seed.quantize(Decimal("0.0001"))
    multiplier = Decimal(2) / Decimal(period + 1)

    ema = sma_seed
    for i in range(period, len(prices)):
        ema = (prices[i] - ema) * multiplier + ema
        result[i] = ema.quantize(Decimal("0.0001"))

    return result


def calculate_rsi(prices: Sequence[Decimal], period: int = 14) -> list[Decimal | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(prices) < period + 1:
        return [None] * len(prices)

    result: list[Decimal | None] = [None] * len(prices)
    gains: list[Decimal] = []
    losses: list[Decimal] = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))

    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    def _rsi(ag: Decimal, al: Decimal) -> Decimal:
        if al == 0:
            return Decimal("100")
        rs = ag / al
        return (Decimal("100") - (Decimal("100") / (Decimal("1") + rs))).quantize(Decimal("0.01"))

    result[period] = _rsi(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)
        result[i + 1] = _rsi(avg_gain, avg_loss)

    return result
