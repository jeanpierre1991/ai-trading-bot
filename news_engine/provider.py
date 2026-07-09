"""News provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class NewsArticle:
    headline: str
    summary: str
    symbol: str
    sentiment_score: float
    published_at: datetime
    source: str


class NewsProvider(ABC):
    @abstractmethod
    def fetch_headlines(self, symbol: str, limit: int = 10) -> list[NewsArticle]:
        ...


class MockNewsProvider(NewsProvider):
    _HEADLINES = (
        ("Earnings beat expectations", 0.6),
        ("Analyst upgrades price target", 0.4),
        ("Sector rotation impacts demand", -0.1),
        ("Supply chain concerns ease", 0.3),
        ("Regulatory review announced", -0.5),
    )

    def fetch_headlines(self, symbol: str, limit: int = 10) -> list[NewsArticle]:
        articles: list[NewsArticle] = []
        now = datetime.now(timezone.utc)

        for i, (headline, sentiment) in enumerate(self._HEADLINES[:limit]):
            articles.append(
                NewsArticle(
                    headline=f"{symbol}: {headline}",
                    summary=f"Mock article about {symbol} — {headline.lower()}.",
                    symbol=symbol,
                    sentiment_score=sentiment,
                    published_at=now,
                    source="mock_news",
                )
            )
        return articles
