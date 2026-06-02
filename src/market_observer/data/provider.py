"""Market-data provider abstraction.

The rest of the system depends only on this Protocol, never on yfinance
directly, so swapping in Polygon later means writing one new implementation.

All methods may return ``None``/empty on failure — callers degrade gracefully
and never fabricate data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from market_observer.domain.models import EventInfo
from market_observer.domain.options_math import ExpiryChain


@dataclass(frozen=True)
class OHLCV:
    """Price/volume history, oldest-first."""

    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]


@runtime_checkable
class MarketDataProvider(Protocol):
    # --- equities ---
    def get_history(self, symbol: str, lookback_days: int) -> OHLCV | None: ...

    def get_sp500_universe(self) -> list[str]: ...

    def get_avg_volume(self, symbols: Sequence[str], period: int) -> dict[str, float]: ...

    # --- options (implemented for T-06) ---
    def get_option_expiries(self, symbol: str) -> list[date]: ...

    def get_option_chain(self, symbol: str, expiry: date) -> ExpiryChain | None: ...

    # --- macro / events (implemented for T-06) ---
    def get_macro_quote(self, symbol: str) -> tuple[float | None, float | None]: ...

    def get_event_info(self, symbol: str) -> EventInfo | None: ...
