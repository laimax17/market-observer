"""Watchlist selection: S&P 500 top-N by 20-day average volume.

``select_watchlist`` is a pure function (ranking logic) so it is unit-testable
without network. ``build_watchlist`` wires it to a provider.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from .provider import MarketDataProvider

logger = logging.getLogger(__name__)


def select_watchlist(
    avg_volumes: dict[str, float],
    pinned: Sequence[str],
    size: int,
) -> list[str]:
    """Return the watchlist: pinned symbols first (priority), then the highest
    average-volume names, deduped, capped at ``size``."""
    pinned_norm: list[str] = []
    for p in pinned:
        u = p.strip().upper()
        if u and u not in pinned_norm:
            pinned_norm.append(u)

    result = pinned_norm[:size]
    if len(result) >= size:
        return result

    ranked = sorted(
        (s for s in avg_volumes if s not in pinned_norm),
        key=lambda s: avg_volumes[s],
        reverse=True,
    )
    for s in ranked:
        if len(result) >= size:
            break
        result.append(s)
    return result


def build_watchlist(
    provider: MarketDataProvider,
    pinned: Sequence[str],
    size: int,
    avg_volume_period: int = 20,
) -> list[str]:
    """Fetch the S&P 500 universe + average volumes and select the watchlist."""
    universe = provider.get_sp500_universe()
    if not universe:
        logger.warning("empty S&P 500 universe; falling back to pinned only")
        return select_watchlist({}, pinned, size)
    avg_volumes = provider.get_avg_volume(universe, avg_volume_period)
    return select_watchlist(avg_volumes, pinned, size)
