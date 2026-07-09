"""AI-powered market analysis engine."""

from __future__ import annotations

from ai_engine.analyzer import AIAnalyzer, MockAIAnalyzer
from ai_engine.module import AIEngineModule

MODULE_CLASS = AIEngineModule

__all__ = ["AIEngineModule", "AIAnalyzer", "MockAIAnalyzer", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(AIEngineModule)
