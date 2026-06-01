"""Technical indicators — pure functions over sequences of floats.

Deliberately dependency-free (no pandas/numpy) so the domain stays pure and
exactly unit-testable. Each function returns ``None`` when there is not enough
history to compute it, rather than guessing.

Conventions:
* ``closes`` etc. are oldest-first; the last element is the most recent bar.
* RSI/ATR use Wilder's smoothing; MACD/EMA use the standard 2/(n+1) multiplier
  seeded with an SMA.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .models import TechnicalIndicators

TRADING_DAYS_PER_YEAR = 252


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema_series(values: Sequence[float], period: int) -> list[float] | None:
    """Return the EMA series aligned to ``values[period-1:]`` (seeded by SMA)."""
    if period <= 0 or len(values) < period:
        return None
    mult = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append((v - out[-1]) * mult + out[-1])
    return out


def ema(values: Sequence[float], period: int) -> float | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI. Needs at least ``period + 1`` closes."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes[:-1], closes[1:], strict=False):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, ls in zip(gains[period:], losses[period:], strict=False):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + ls) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float] | None:
    """Return ``(macd_line, signal_line, histogram)`` for the latest bar."""
    if len(closes) < slow + signal:
        return None
    fast_s = ema_series(closes, fast)
    slow_s = ema_series(closes, slow)
    if fast_s is None or slow_s is None:
        return None
    # Align both EMA series to the same (most recent) tail length.
    n = min(len(fast_s), len(slow_s))
    macd_line = [f - s for f, s in zip(fast_s[-n:], slow_s[-n:], strict=False)]
    sig_s = ema_series(macd_line, signal)
    if sig_s is None:
        return None
    macd_v = macd_line[-1]
    signal_v = sig_s[-1]
    return macd_v, signal_v, macd_v - signal_v


def realized_vol(closes: Sequence[float], period: int = 20) -> float | None:
    """Annualized realized volatility (percent) from daily log returns."""
    if len(closes) < period + 1:
        return None
    rets: list[float] = []
    for prev, cur in zip(closes[-(period + 1) : -1], closes[-period:], strict=False):
        if prev <= 0 or cur <= 0:
            return None
        rets.append(math.log(cur / prev))
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Wilder's Average True Range. Needs ``period + 1`` bars."""
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return None
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr_v = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_v = (atr_v * (period - 1) + tr) / period
    return atr_v


def range_position(values: Sequence[float], period: int) -> float | None:
    """Where the latest value sits within its ``period`` window, 0..100."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    lo, hi = min(window), max(window)
    if hi == lo:
        return 50.0
    return (window[-1] - lo) / (hi - lo) * 100.0


def rel_volume(volumes: Sequence[float], period: int = 20) -> float | None:
    """Latest volume relative to the average of the preceding ``period`` bars."""
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-(period + 1) : -1]) / period
    if avg <= 0:
        return None
    return volumes[-1] / avg


def _pct_vs(price: float | None, level: float | None) -> float | None:
    if price is None or level is None or level == 0:
        return None
    return (price / level - 1.0) * 100.0


def compute_indicators(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    range_window: int = 60,
) -> TechnicalIndicators:
    """Assemble a ``TechnicalIndicators`` from OHLCV history (oldest-first)."""
    last = closes[-1] if closes else None
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    macd_t = macd(closes)
    return TechnicalIndicators(
        rsi_14=rsi(closes, 14),
        macd=macd_t[0] if macd_t else None,
        macd_signal=macd_t[1] if macd_t else None,
        macd_hist=macd_t[2] if macd_t else None,
        sma_20=sma20,
        sma_50=sma50,
        sma_200=sma200,
        price_vs_sma20_pct=_pct_vs(last, sma20),
        price_vs_sma50_pct=_pct_vs(last, sma50),
        price_vs_sma200_pct=_pct_vs(last, sma200),
        realized_vol_20=realized_vol(closes, 20),
        atr_14=atr(highs, lows, closes, 14),
        range_position_pct=range_position(closes, range_window),
        rel_volume=rel_volume(volumes, 20),
    )
