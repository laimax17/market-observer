"""Recent-news builder: caps count and degrades to [] on failure."""

from __future__ import annotations

from datetime import date

from market_observer.data.news import build_recent_news
from market_observer.domain.models import NewsItem


class _OkProvider:
    def get_recent_news(self, symbol: str, limit: int) -> list[NewsItem]:
        items = [NewsItem(title=f"{symbol} headline {i}") for i in range(10)]
        return items[:limit]


class _BoomProvider:
    def get_recent_news(self, symbol: str, limit: int) -> list[NewsItem]:
        raise RuntimeError("rate limited")


def test_build_recent_news_caps_limit() -> None:
    news = build_recent_news(_OkProvider(), "AAPL", limit=3)
    assert len(news) == 3
    assert all(isinstance(n, NewsItem) for n in news)


def test_build_recent_news_degrades_to_empty() -> None:
    assert build_recent_news(_BoomProvider(), "AAPL") == []


def test_news_item_optional_fields() -> None:
    n = NewsItem(title="t", published=date(2026, 6, 1))
    assert n.publisher is None
    assert n.url is None
