"""Build the macro backdrop (VIX, USD, yields, commodities)."""

from __future__ import annotations

from datetime import date

from market_observer.domain.models import MacroContext, MacroQuote

from .provider import MarketDataProvider

# (human label, source ticker)
MACRO_INSTRUMENTS: tuple[tuple[str, str], ...] = (
    ("VIX", "^VIX"),
    ("US Dollar Index", "DX-Y.NYB"),
    ("US 10Y Yield", "^TNX"),
    ("WTI Crude", "CL=F"),
    ("Gold", "GC=F"),
)


def build_macro_context(
    provider: MarketDataProvider,
    as_of: date,
    instruments: tuple[tuple[str, str], ...] = MACRO_INSTRUMENTS,
) -> MacroContext:
    quotes: list[MacroQuote] = []
    for name, ticker in instruments:
        value, pct = provider.get_macro_quote(ticker)
        quotes.append(MacroQuote(name=name, symbol=ticker, value=value, pct_change_1d=pct))
    return MacroContext(as_of=as_of, quotes=quotes)
