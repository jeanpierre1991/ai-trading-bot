"""News ingestion and sentiment analysis."""

from __future__ import annotations

from news_engine.module import NewsEngineModule
from news_engine.provider import MockNewsProvider, NewsArticle, NewsProvider

MODULE_CLASS = NewsEngineModule

__all__ = ["NewsEngineModule", "NewsProvider", "MockNewsProvider", "NewsArticle", "MODULE_CLASS"]


def register_modules(registry: object) -> None:
    from core.module_registry import ModuleRegistry

    if isinstance(registry, ModuleRegistry):
        registry.register_class(NewsEngineModule)
