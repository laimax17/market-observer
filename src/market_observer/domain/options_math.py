"""Options signals — pure functions over a normalized option chain.

Input is a list of ``ExpiryChain`` (one per expiry, nearest first). These are
plain value objects so the domain has no dependency on yfinance; the data
layer is responsible for fetching and normalizing into this shape.

All signals return ``None`` (or ``has_data=False``) when the chain is too thin
to compute them honestly. Nothing is fabricated.

Definitions:
* ATM IV: average of the call and put implied vols whose strikes are nearest
  to spot (whichever side has data).
* term structure: ``next_atm_iv - front_atm_iv``; negative => front higher =>
  backwardation/inversion (often event pricing).
* put/call ratios: summed over the front expiry.
* IV skew: OTM-put IV (strike ~ spot*(1-k)) minus OTM-call IV
  (strike ~ spot*(1+k)). Positive => downside protection bid up.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from .models import OptionsSignal

DEFAULT_OTM_PCT = 0.05
CALENDAR_DAYS_PER_YEAR = 365.0


def implied_move_pct(atm_iv_value: float | None, days_to_expiry: int | None) -> float | None:
    """1-sigma expected move (percent) to expiry from annualized ATM IV:
    ``IV * sqrt(days/365) * 100``. None if inputs are missing/non-positive."""
    if atm_iv_value is None or days_to_expiry is None or days_to_expiry <= 0 or atm_iv_value <= 0:
        return None
    return atm_iv_value * math.sqrt(days_to_expiry / CALENDAR_DAYS_PER_YEAR) * 100.0


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    implied_vol: float | None = None
    volume: float | None = None
    open_interest: float | None = None


@dataclass(frozen=True)
class ExpiryChain:
    expiry: date
    calls: list[OptionQuote] = field(default_factory=list)
    puts: list[OptionQuote] = field(default_factory=list)


def _nearest_with_iv(quotes: Sequence[OptionQuote], target: float) -> OptionQuote | None:
    candidates = [q for q in quotes if q.implied_vol is not None and q.implied_vol > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda q: abs(q.strike - target))


def atm_iv(chain: ExpiryChain, spot: float) -> float | None:
    """Average of nearest-strike call and put IVs around spot."""
    call = _nearest_with_iv(chain.calls, spot)
    put = _nearest_with_iv(chain.puts, spot)
    ivs = [q.implied_vol for q in (call, put) if q is not None and q.implied_vol is not None]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def term_structure(front: float | None, nxt: float | None) -> float | None:
    if front is None or nxt is None:
        return None
    return nxt - front


def _sum_field(quotes: Sequence[OptionQuote], attr: str) -> float:
    total = 0.0
    for q in quotes:
        v = getattr(q, attr)
        if v is not None and v > 0:
            total += v
    return total


def put_call_ratio(chain: ExpiryChain, attr: str) -> float | None:
    calls = _sum_field(chain.calls, attr)
    puts = _sum_field(chain.puts, attr)
    if calls <= 0:
        return None
    return puts / calls


def iv_skew(chain: ExpiryChain, spot: float, otm_pct: float = DEFAULT_OTM_PCT) -> float | None:
    """OTM put IV minus OTM call IV at ``otm_pct`` away from spot."""
    if spot <= 0:
        return None
    put = _nearest_with_iv(chain.puts, spot * (1 - otm_pct))
    call = _nearest_with_iv(chain.calls, spot * (1 + otm_pct))
    if put is None or call is None:
        return None
    assert put.implied_vol is not None and call.implied_vol is not None
    return put.implied_vol - call.implied_vol


def compute_options_signal(
    symbol: str,
    spot: float | None,
    expiries: Sequence[ExpiryChain],
    otm_pct: float = DEFAULT_OTM_PCT,
    as_of: date | None = None,
) -> OptionsSignal:
    """Assemble an ``OptionsSignal`` from a normalized chain (nearest first)."""
    if spot is None or spot <= 0 or not expiries:
        return OptionsSignal(symbol=symbol, has_data=False, note="no option chain / spot")

    front = expiries[0]
    nxt = expiries[1] if len(expiries) > 1 else None

    front_iv = atm_iv(front, spot)
    next_iv = atm_iv(nxt, spot) if nxt is not None else None
    ts = term_structure(front_iv, next_iv)
    inverted = (ts < 0) if ts is not None else None

    dte = (front.expiry - as_of).days if as_of is not None else None
    move = implied_move_pct(front_iv, dte)

    signal = OptionsSignal(
        symbol=symbol,
        has_data=True,
        front_atm_iv=front_iv,
        next_atm_iv=next_iv,
        term_structure=ts,
        term_structure_inverted=inverted,
        put_call_volume_ratio=put_call_ratio(front, "volume"),
        put_call_oi_ratio=put_call_ratio(front, "open_interest"),
        iv_skew=iv_skew(front, spot, otm_pct),
        front_days_to_expiry=dte,
        implied_move_pct=move,
    )
    # If literally nothing computed, flag it rather than presenting empty data.
    if all(
        v is None
        for v in (
            signal.front_atm_iv,
            signal.put_call_volume_ratio,
            signal.put_call_oi_ratio,
            signal.iv_skew,
        )
    ):
        signal.has_data = False
        signal.note = "option chain too thin"
    return signal
