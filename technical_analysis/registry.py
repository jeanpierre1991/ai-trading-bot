"""Indicator registry mapping names to existing implementations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from technical_analysis.indicators import calculate_ema, calculate_rsi, calculate_sma
from technical_analysis.types import IndicatorSource

IndicatorFn = Callable[[Sequence[Decimal], int], list[Decimal | None]]


@dataclass(frozen=True)
class IndicatorDefinition:
    name: str
    function: IndicatorFn
    default_params: dict[str, Any]
    default_source: IndicatorSource = IndicatorSource.CLOSE


INDICATOR_REGISTRY: dict[str, IndicatorDefinition] = {
    "sma": IndicatorDefinition(
        name="sma",
        function=calculate_sma,
        default_params={"period": 20},
    ),
    "ema": IndicatorDefinition(
        name="ema",
        function=calculate_ema,
        default_params={"period": 20},
    ),
    "rsi": IndicatorDefinition(
        name="rsi",
        function=calculate_rsi,
        default_params={"period": 14},
    ),
}


def get_indicator(name: str) -> IndicatorDefinition:
    normalized_name = name.strip().lower()
    try:
        return INDICATOR_REGISTRY[normalized_name]
    except KeyError as exc:
        raise ValueError(f"Unknown indicator: '{name}'") from exc


def list_indicators() -> tuple[str, ...]:
    return tuple(sorted(INDICATOR_REGISTRY))


def build_result_key(name: str, params: dict[str, Any]) -> str:
    period = params.get("period")
    if period is not None:
        return f"{name}_{period}"
    return name
