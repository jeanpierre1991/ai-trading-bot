"""Technical analysis indicators and signals."""

from __future__ import annotations

from technical_analysis.indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from technical_analysis.module import TechnicalAnalysisModule

MODULE_CLASS = TechnicalAnalysisModule

__all__ = [
    "TechnicalAnalysisModule",
    "calculate_sma",
    "calculate_ema",
    "calculate_rsi",
    "MODULE_CLASS",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(TechnicalAnalysisModule)
