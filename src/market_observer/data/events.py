"""Build upcoming-event info (earnings / ex-dividend) for a symbol."""

from __future__ import annotations

from datetime import date

from market_observer.domain.models import EventInfo

from .provider import MarketDataProvider


def build_event_info(
    provider: MarketDataProvider,
    symbol: str,
    as_of: date,
) -> EventInfo | None:
    info = provider.get_event_info(symbol)
    if info is None:
        return None
    # Fill days_to_earnings from as_of when the provider didn't.
    if info.next_earnings_date is not None and info.days_to_earnings is None:
        return info.model_copy(
            update={"days_to_earnings": (info.next_earnings_date - as_of).days}
        )
    return info
