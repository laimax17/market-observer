"""Shared helpers for agents: JSON extraction and fact formatting.

Agents interpret ONLY the structured facts the code computed. The prompts
forbid inventing data and forbid buy/sell or confidence numbers (design §6).
"""

from __future__ import annotations

import json
from typing import Any

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
    if o is None or not o.has_data:
        return {"symbol": snap.symbol, "has_data": False, "note": (o.note if o else "no data")}
    return {
        "symbol": snap.symbol,
        "has_data": True,
        "front_atm_iv": _r(o.front_atm_iv, 4),
        "next_atm_iv": _r(o.next_atm_iv, 4),
        "term_structure": _r(o.term_structure, 4),
        "term_structure_inverted": o.term_structure_inverted,
        "put_call_volume_ratio": _r(o.put_call_volume_ratio),
        "put_call_oi_ratio": _r(o.put_call_oi_ratio),
        "iv_skew": _r(o.iv_skew, 4),
    }


def macro_facts(data: BriefingData) -> list[dict[str, Any]]:
    return [
        {"name": q.name, "value": _r(q.value), "pct_change_1d": _r(q.pct_change_1d)}
        for q in data.macro.quotes
    ]


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
