"""Fetch recent news headlines for a symbol (grounds the 'why' section).

Thin wrapper over the provider: caps the count and degrades to an empty list
on any failure, so a missing/rate-limited news feed never breaks the briefing.
"""

from __future__ import annotations

import logging

from market_observer.domain.models import NewsItem

from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

DEFAULT_NEWS_LIMIT = 5


def build_recent_news(
    provider: MarketDataProvider,
    symbol: str,
    limit: int = DEFAULT_NEWS_LIMIT,
) -> list[NewsItem]:
    try:
        return provider.get_recent_news(symbol, limit)[:limit]
    except Exception as exc:  # noqa: BLE001 - news is best-effort
        logger.warning("news build failed for %s: %s", symbol, exc)
        return []
