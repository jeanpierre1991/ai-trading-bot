"""News engine module implementation."""

from __future__ import annotations

from news_engine.provider import MockNewsProvider, NewsProvider
from config.settings import Settings
from core.base_module import BaseModule, ModuleHealth


class NewsEngineModule(BaseModule):
    """Aggregates news and computes sentiment signals."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._provider: NewsProvider | None = None

    @property
    def name(self) -> str:
        return "news_engine"

    def _on_initialize(self) -> None:
        provider = self._settings.news_provider.lower()
        if provider == "mock":
            self._provider = MockNewsProvider()
        else:
            self._provider = MockNewsProvider()
            self.logger.warning("News provider '%s' not implemented; using mock", provider)

    def get_sentiment(self, symbol: str | None = None) -> dict[str, float | int | str]:
        if self._provider is None:
            raise RuntimeError("News provider not initialized")

        sym = symbol or self._settings.default_symbol
        articles = self._provider.fetch_headlines(sym, limit=5)
        if not articles:
            return {"symbol": sym, "sentiment": 0.0, "article_count": 0}

        avg_sentiment = sum(a.sentiment_score for a in articles) / len(articles)
        return {
            "symbol": sym,
            "sentiment": round(avg_sentiment, 3),
            "article_count": len(articles),
            "headlines": [a.headline for a in articles[:3]],
        }

    def health_check(self) -> ModuleHealth:
        if self._provider is None:
            return self._unhealthy("News provider not initialized")

        try:
            result = self.get_sentiment()
            return self._healthy(
                message="News provider operational",
                provider=self._settings.news_provider,
                sentiment=result["sentiment"],
                articles=result["article_count"],
            )
        except Exception as exc:
            return self._unhealthy(f"Health check failed: {exc}")
