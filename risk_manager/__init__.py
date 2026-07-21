"""Risk management and position sizing."""

from __future__ import annotations

from risk_manager.base import RiskManager
from risk_manager.basic import BasicRiskManager
from risk_manager.models import RiskEvaluation
from risk_manager.module import RiskManagerModule
from risk_manager.rules import RiskAssessment, RiskRules

MODULE_CLASS = RiskManagerModule

__all__ = [
    "BasicRiskManager",
    "MODULE_CLASS",
    "RiskAssessment",
    "RiskEvaluation",
    "RiskManager",
    "RiskManagerModule",
    "RiskRules",
]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(RiskManagerModule)
