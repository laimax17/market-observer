"""Build an OptionsSignal for a symbol via the provider's option chain."""

from __future__ import annotations

import logging
from datetime import date

from market_observer.domain.models import OptionsSignal
from market_observer.domain.options_math import compute_options_signal

from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

DEFAULT_MAX_EXPIRIES = 2  # front + next is enough for term structure


def build_options_signal(
    provider: MarketDataProvider,
    symbol: str,
    spot: float | None,
    max_expiries: int = DEFAULT_MAX_EXPIRIES,
    as_of: date | None = None,
) -> OptionsSignal:
    expiries = provider.get_option_expiries(symbol)
    if not expiries:
        return OptionsSignal(symbol=symbol, has_data=False, note="no option expiries")

    chains = []
    for exp in expiries[:max_expiries]:
        chain = provider.get_option_chain(symbol, exp)
        if chain is not None:
            chains.append(chain)

    if not chains:
        return OptionsSignal(symbol=symbol, has_data=False, note="option chain unavailable")

    return compute_options_signal(symbol, spot, chains, as_of=as_of)
