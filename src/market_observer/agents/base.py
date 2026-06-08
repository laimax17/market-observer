"""Shared helpers for agents: JSON extraction and fact formatting.

Agents interpret ONLY the structured facts the code computed. The prompts
forbid inventing data and forbid buy/sell or confidence numbers (design §6).
"""

from __future__ import annotations

import json
from typing import Any

from market_observer.domain.forecast import forecast_levels
from market_observer.domain.models import BriefingData, SymbolSnapshot


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response, tolerating code fences and
    surrounding prose. Raises ValueError if no object can be found."""
    s = text.strip()
    if s.startswith("```"):
        # Drop the first fence line and any trailing fence.
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in response")
    return json.loads(s[start : end + 1])


def _r(value: float | None, ndigits: int = 2) -> float | None:
    return round(value, ndigits) if value is not None else None


def technical_facts(snap: SymbolSnapshot) -> dict[str, Any]:
    t = snap.technicals
    return {
        "symbol": snap.symbol,
        "last_price": _r(snap.last_price),
        "rsi_14": _r(t.rsi_14),
        "macd_hist": _r(t.macd_hist, 4),
        "price_vs_sma20_pct": _r(t.price_vs_sma20_pct),
        "price_vs_sma50_pct": _r(t.price_vs_sma50_pct),
        "price_vs_sma200_pct": _r(t.price_vs_sma200_pct),
        "realized_vol_20_pct": _r(t.realized_vol_20),
        "atr_14": _r(t.atr_14),
        "range_position_pct": _r(t.range_position_pct),
        "rel_volume": _r(t.rel_volume),
    }


def options_facts(snap: SymbolSnapshot) -> dict[str, Any]:
    o = snap.options
    ev = snap.event
    days_to_earnings = ev.days_to_earnings if ev else None
    if o is None or not o.has_data:
        return {
            "symbol": snap.symbol,
            "has_data": False,
            "note": (o.note if o else "no data"),
            "days_to_earnings": days_to_earnings,
        }
    return {
        "symbol": snap.symbol,
        "has_data": True,
        "front_atm_iv": _r(o.front_atm_iv, 4),
        "next_atm_iv": _r(o.next_atm_iv, 4),
        "term_structure": _r(o.term_structure, 4),
        "term_structure_inverted": o.term_structure_inverted,
        "front_days_to_expiry": o.front_days_to_expiry,
        "implied_move_pct": _r(o.implied_move_pct),
        "put_call_volume_ratio": _r(o.put_call_volume_ratio),
        "put_call_oi_ratio": _r(o.put_call_oi_ratio),
        "iv_skew": _r(o.iv_skew, 4),
        # Context for judging whether IV is rich/cheap and why it may be inverted.
        "realized_vol_20_pct": _r(snap.technicals.realized_vol_20),
        "days_to_earnings": days_to_earnings,
    }


def macro_facts(data: BriefingData) -> list[dict[str, Any]]:
    return [
        {"name": q.name, "value": _r(q.value), "pct_change_1d": _r(q.pct_change_1d)}
        for q in data.macro.quotes
    ]


def recent_facts(snap: SymbolSnapshot) -> dict[str, Any]:
    """Short-horizon price action + upcoming events: grounds 'what happened'."""
    r = snap.recent
    ev = snap.event
    return {
        "ret_1d_pct": _r(r.ret_1d_pct) if r else None,
        "ret_5d_pct": _r(r.ret_5d_pct) if r else None,
        "ret_20d_pct": _r(r.ret_20d_pct) if r else None,
        "days_to_earnings": ev.days_to_earnings if ev else None,
    }


def news_facts(snap: SymbolSnapshot) -> list[dict[str, Any]]:
    """The ONLY headlines the model may use to attribute causes."""
    return [
        {
            "title": n.title,
            "publisher": n.publisher,
            "published": n.published.isoformat() if n.published else None,
        }
        for n in snap.news
    ]


def forecast_facts(snap: SymbolSnapshot) -> dict[str, Any]:
    """Data-grounded levels to anchor the forecast (option-implied band, ATR
    band, moving averages). The model picks a direction *within* these."""
    lv = forecast_levels(snap)
    return {k: _r(v) for k, v in lv.items()}


def synthesis_symbol_facts(snap: SymbolSnapshot) -> dict[str, Any]:
    """Everything the synthesizer needs to write the 4-part narrative."""
    return {
        "symbol": snap.symbol,
        "recent": recent_facts(snap),
        "news": news_facts(snap),
        "levels": forecast_facts(snap),
    }


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
