"""Indicator orchestration over market bars."""

from __future__ import annotations

from decimal import Decimal

from core.types import MarketBar
from technical_analysis.registry import build_result_key, get_indicator
from technical_analysis.series import extract_series
from technical_analysis.types import IndicatorRequest, IndicatorSnapshot


class IndicatorCalculator:
    """Orchestrates indicator computation without reimplementing algorithms."""

    @staticmethod
    def compute(
        bars: list[MarketBar],
        requests: list[IndicatorRequest],
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> IndicatorSnapshot:
        if not requests:
            return IndicatorSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=bars[-1].timestamp if bars else None,
                values={},
                series={},
            )

        values: dict[str, Decimal | None] = {}
        series: dict[str, list[Decimal | None]] = {}

        for request in requests:
            definition = get_indicator(request.name)
            merged_params = {**definition.default_params, **request.params}
            period = _validated_period(merged_params.get("period"))

            prices = extract_series(bars, request.source)
            result_key = build_result_key(request.name, merged_params)
            computed = definition.function(prices, period)

            series[result_key] = computed
            values[result_key] = _latest_non_null(computed)

        resolved_symbol = symbol
        resolved_timeframe = timeframe
        if bars:
            if resolved_symbol is None and "symbol" in bars[-1]:
                resolved_symbol = str(bars[-1]["symbol"])
            if resolved_timeframe is None and "timeframe" in bars[-1]:
                resolved_timeframe = str(bars[-1]["timeframe"])

        return IndicatorSnapshot(
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            timestamp=bars[-1].timestamp if bars else None,
            values=values,
            series=series,
        )


def _validated_period(period: object) -> int:
    if not isinstance(period, int):
        raise ValueError("period must be an integer")
    if period <= 0:
        raise ValueError("period must be positive")
    return period


def _latest_non_null(values: list[Decimal | None]) -> Decimal | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None
