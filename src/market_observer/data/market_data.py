"""Build the technical part of a SymbolSnapshot from a provider's history."""

from __future__ import annotations

import logging
from datetime import date

from market_observer.domain.forecast import compute_recent_action
from market_observer.domain.indicators import compute_indicators
from market_observer.domain.models import SymbolSnapshot

from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

# ~1 trading year of history so SMA200 is computable.
DEFAULT_LOOKBACK_DAYS = 300


def build_symbol_snapshot(
    provider: MarketDataProvider,
    symbol: str,
    as_of: date,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> SymbolSnapshot:
    """Snapshot with technicals only (options/events attached later in T-06)."""
    hist = provider.get_history(symbol, lookback_days)
    if hist is None or not hist.closes:
        logger.warning("no history for %s", symbol)
        return SymbolSnapshot(symbol=symbol, as_of=as_of)

    tech = compute_indicators(hist.closes, hist.highs, hist.lows, hist.volumes)
    return SymbolSnapshot(
        symbol=symbol,
        as_of=as_of,
        last_price=hist.closes[-1],
        technicals=tech,
        recent=compute_recent_action(hist.closes),
    )
