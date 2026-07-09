"""Core framework for the AI Trading Bot."""

from core.base_module import BaseModule, ModuleHealth, ModuleStatus
from core.module_registry import ModuleRegistry

__all__ = [
    "BaseModule",
    "ModuleHealth",
    "ModuleStatus",
    "ModuleRegistry",
    "TradingBotApplication",
]


def __getattr__(name: str):
    if name == "TradingBotApplication":
        from core.application import TradingBotApplication

        return TradingBotApplication
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
