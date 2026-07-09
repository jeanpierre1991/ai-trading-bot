"""Risk management and position sizing."""

from __future__ import annotations

from risk_manager.module import RiskManagerModule
from risk_manager.rules import RiskAssessment, RiskRules

MODULE_CLASS = RiskManagerModule

__all__ = ["RiskManagerModule", "RiskRules", "RiskAssessment", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(RiskManagerModule)
