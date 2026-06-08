"""Code-computed inputs for the 'recent action' and 'forecast' sections.

Pure functions, no network/LLM. Two jobs:

* ``compute_recent_action`` — short-horizon returns, so the briefing can state
  *what happened* from data instead of relying on the LLM's stale memory.
* ``forecast_levels`` — data-grounded price levels (option-implied 1-sigma band,
  ATR band, moving averages) that anchor the LLM's directional view and are
  rendered directly even when no LLM is configured. The model supplies the
  bias/odds; the *levels* come from the data, not the model's imagination.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import RecentAction, SymbolSnapshot


def pct_return(closes: Sequence[float], period: int) -> float | None:
    """Percent return over the last ``period`` bars. None if insufficient
    history or a non-positive base price."""
    if period <= 0 or len(closes) < period + 1:
        return None
    base = closes[-period - 1]
    if base <= 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def compute_recent_action(closes: Sequence[float]) -> RecentAction:
    return RecentAction(
        ret_1d_pct=pct_return(closes, 1),
        ret_5d_pct=pct_return(closes, 5),
        ret_20d_pct=pct_return(closes, 20),
    )


def forecast_levels(snap: SymbolSnapshot) -> dict[str, float | None]:
    """Data-grounded key levels for the forecast section.

    Keys: ``last``, ``implied_move_pct``, ``implied_low``, ``implied_high``,
    ``atr_low``, ``atr_high``, ``sma20``, ``sma50``, ``sma200``. Values are
    ``None`` when the underlying inputs are missing.
    """
    last = snap.last_price
    t = snap.technicals
    o = snap.options

    move = o.implied_move_pct if (o is not None and o.has_data) else None
    implied_low = implied_high = None
    if last is not None and move is not None:
        implied_low = last * (1 - move / 100.0)
        implied_high = last * (1 + move / 100.0)

    atr_low = atr_high = None
    if last is not None and t.atr_14 is not None:
        atr_low = last - t.atr_14
        atr_high = last + t.atr_14

    return {
        "last": last,
        "implied_move_pct": move,
        "implied_low": implied_low,
        "implied_high": implied_high,
        "atr_low": atr_low,
        "atr_high": atr_high,
        "sma20": t.sma_20,
        "sma50": t.sma_50,
        "sma200": t.sma_200,
    }
