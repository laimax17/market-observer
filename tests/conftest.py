"""Shared test fakes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from market_observer.data.provider import OHLCV
from market_observer.domain.models import EventInfo
from market_observer.domain.options_math import ExpiryChain


class FakeProvider:
    """In-memory MarketDataProvider for tests. Configure per-symbol data."""

    def __init__(
        self,
        histories: dict[str, OHLCV] | None = None,
        universe: list[str] | None = None,
        avg_volumes: dict[str, float] | None = None,
        expiries: dict[str, list[date]] | None = None,
        chains: dict[tuple[str, date], ExpiryChain] | None = None,
        macro: dict[str, tuple[float | None, float | None]] | None = None,
        events: dict[str, EventInfo] | None = None,
    ) -> None:
        self._histories = histories or {}
        self._universe = universe or []
        self._avg_volumes = avg_volumes or {}
        self._expiries = expiries or {}
        self._chains = chains or {}
        self._macro = macro or {}
        self._events = events or {}

    def get_history(self, symbol: str, lookback_days: int) -> OHLCV | None:
        return self._histories.get(symbol.upper())

    def get_sp500_universe(self) -> list[str]:
        return list(self._universe)

    def get_avg_volume(self, symbols: Sequence[str], period: int) -> dict[str, float]:
        return {s: self._avg_volumes[s] for s in symbols if s in self._avg_volumes}

    def get_option_expiries(self, symbol: str) -> list[date]:
        return list(self._expiries.get(symbol.upper(), []))

    def get_option_chain(self, symbol: str, expiry: date) -> ExpiryChain | None:
        return self._chains.get((symbol.upper(), expiry))

    def get_macro_quote(self, symbol: str) -> tuple[float | None, float | None]:
        return self._macro.get(symbol, (None, None))

    def get_event_info(self, symbol: str) -> EventInfo | None:
        return self._events.get(symbol.upper())


def make_ohlcv(n: int = 250, start: float = 100.0, step: float = 0.5) -> OHLCV:
    closes = [start + i * step for i in range(n)]
    return OHLCV(
        closes=closes,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        volumes=[1_000_000.0 + (i % 5) * 10_000 for i in range(n)],
    )
