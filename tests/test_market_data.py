"""T-05: snapshot building from a provider's history."""

from __future__ import annotations

from datetime import date

from market_observer.data.market_data import build_symbol_snapshot

from .conftest import FakeProvider, make_ohlcv


def test_build_snapshot_with_history() -> None:
    provider = FakeProvider(histories={"AAPL": make_ohlcv(250)})
    snap = build_symbol_snapshot(provider, "aapl", date(2026, 6, 1))
    assert snap.symbol == "AAPL"
    assert snap.last_price is not None
    assert snap.technicals.sma_200 is not None
    assert snap.technicals.rsi_14 is not None


def test_build_snapshot_no_history_is_empty_but_valid() -> None:
    provider = FakeProvider(histories={})
    snap = build_symbol_snapshot(provider, "ZZZZ", date(2026, 6, 1))
    assert snap.symbol == "ZZZZ"
    assert snap.last_price is None
    assert snap.technicals.rsi_14 is None
