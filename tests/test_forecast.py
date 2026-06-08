"""Code-computed recent action + forecast levels (domain/forecast.py)."""

from __future__ import annotations

from datetime import date

import pytest

from market_observer.domain.forecast import (
    compute_recent_action,
    forecast_levels,
    pct_return,
)
from market_observer.domain.models import OptionsSignal, SymbolSnapshot, TechnicalIndicators


def test_pct_return_basic_and_guards() -> None:
    closes = [100.0, 110.0]
    assert pct_return(closes, 1) == pytest.approx(10.0)
    assert pct_return(closes, 5) is None  # insufficient history
    assert pct_return([0.0, 110.0], 1) is None  # non-positive base
    assert pct_return(closes, 0) is None


def test_compute_recent_action() -> None:
    closes = [float(i) for i in range(100, 130)]  # monotonically rising
    ra = compute_recent_action(closes)
    assert ra.ret_1d_pct is not None and ra.ret_1d_pct > 0
    assert ra.ret_5d_pct is not None and ra.ret_5d_pct > 0
    assert ra.ret_20d_pct is not None and ra.ret_20d_pct > 0


def test_forecast_levels_implied_and_atr_bands() -> None:
    snap = SymbolSnapshot(
        symbol="AAA",
        as_of=date(2026, 6, 1),
        last_price=100.0,
        technicals=TechnicalIndicators(atr_14=2.0, sma_20=99.0, sma_50=98.0, sma_200=95.0),
        options=OptionsSignal(symbol="AAA", has_data=True, implied_move_pct=5.0),
    )
    lv = forecast_levels(snap)
    assert lv["last"] == 100.0
    assert lv["implied_move_pct"] == 5.0
    assert lv["implied_low"] == 95.0
    assert lv["implied_high"] == 105.0
    assert lv["atr_low"] == 98.0
    assert lv["atr_high"] == 102.0
    assert lv["sma20"] == 99.0


def test_forecast_levels_missing_inputs_are_none() -> None:
    snap = SymbolSnapshot(symbol="ZZZ", as_of=date(2026, 6, 1))
    lv = forecast_levels(snap)
    assert lv["implied_low"] is None
    assert lv["atr_low"] is None
    assert lv["last"] is None
