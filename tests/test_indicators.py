"""T-03: technical indicator unit tests, including hand-verifiable cases."""

from __future__ import annotations

import math

import pytest

from market_observer.domain.indicators import (
    atr,
    compute_indicators,
    ema,
    macd,
    range_position,
    realized_vol,
    rel_volume,
    rsi,
    sma,
)


# --- SMA ---
def test_sma_basic() -> None:
    assert sma([1, 2, 3, 4, 5], 5) == 3.0
    assert sma([2, 4, 6], 2) == 5.0


def test_sma_insufficient() -> None:
    assert sma([1, 2], 5) is None
    assert sma([], 1) is None


# --- EMA (hand-verified) ---
def test_ema_hand() -> None:
    # seed=(1+2)/2=1.5; then 2.5; then 3.5
    assert ema([1, 2, 3, 4], 2) == pytest.approx(3.5)


def test_ema_insufficient() -> None:
    assert ema([1.0], 2) is None


# --- RSI (hand-verified with period=2) ---
def test_rsi_hand_period2() -> None:
    # closes 10,11,10,11 -> avg_gain 0.75, avg_loss 0.25 -> RS 3 -> RSI 75
    assert rsi([10, 11, 10, 11], period=2) == pytest.approx(75.0)


def test_rsi_all_gains_is_100() -> None:
    assert rsi([1, 2, 3, 4, 5, 6], period=2) == pytest.approx(100.0)


def test_rsi_all_losses_is_0() -> None:
    assert rsi([6, 5, 4, 3, 2, 1], period=2) == pytest.approx(0.0)


def test_rsi_flat_is_50() -> None:
    assert rsi([5, 5, 5, 5], period=2) == pytest.approx(50.0)


def test_rsi_insufficient() -> None:
    assert rsi([1, 2], period=14) is None


# --- MACD ---
def test_macd_insufficient() -> None:
    assert macd([1.0] * 10) is None


def test_macd_shape_and_hist_relation() -> None:
    closes = [100 + i for i in range(60)]  # strictly increasing
    result = macd(closes)
    assert result is not None
    macd_v, signal_v, hist = result
    assert hist == pytest.approx(macd_v - signal_v)
    assert macd_v > 0  # uptrend -> positive macd


# --- realized vol ---
def test_realized_vol_constant_is_zero() -> None:
    assert realized_vol([50.0] * 25, 20) == pytest.approx(0.0)


def test_realized_vol_insufficient() -> None:
    assert realized_vol([1.0] * 5, 20) is None


def test_realized_vol_positive() -> None:
    closes = [100 * (1.01 if i % 2 else 0.99) ** 1 for i in range(25)]
    rv = realized_vol(closes, 20)
    assert rv is not None and rv > 0


# --- ATR (hand-verified) ---
def test_atr_hand_period2() -> None:
    highs = [2, 3, 4]
    lows = [1, 2, 3]
    closes = [1.5, 2.5, 3.5]
    assert atr(highs, lows, closes, period=2) == pytest.approx(1.5)


def test_atr_insufficient() -> None:
    assert atr([1, 2], [1, 2], [1, 2], period=14) is None


# --- range position ---
def test_range_position_extremes() -> None:
    assert range_position([1, 2, 3, 4, 5], 5) == pytest.approx(100.0)
    assert range_position([5, 4, 3, 2, 1], 5) == pytest.approx(0.0)
    assert range_position([3, 3, 3], 3) == pytest.approx(50.0)


# --- relative volume ---
def test_rel_volume_hand() -> None:
    assert rel_volume([10, 10, 10, 20], period=3) == pytest.approx(2.0)


def test_rel_volume_insufficient() -> None:
    assert rel_volume([10, 10], period=20) is None


# --- compute_indicators integration ---
def test_compute_indicators_full() -> None:
    n = 250
    closes = [100 + i * 0.5 for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1_000_000 + (i % 5) * 10_000 for i in range(n)]
    t = compute_indicators(closes, highs, lows, volumes)
    assert t.sma_20 is not None
    assert t.sma_200 is not None
    assert t.rsi_14 is not None
    assert t.macd is not None
    assert t.atr_14 is not None
    assert t.price_vs_sma200_pct is not None
    assert t.range_position_pct == pytest.approx(100.0)  # monotone up
    assert math.isfinite(t.realized_vol_20)
