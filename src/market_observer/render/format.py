"""Shared formatting + signal helpers used by BOTH renderers.

Single source of truth so the Markdown archive and the Discord embeds never
disagree on a number, a label, or a colour. Everything here is pure.

Design notes:
* Implied vols are stored as decimals (0.272) but always shown as percent
  (27.2%) — this kills the old inconsistency where prose said "27%" while the
  data line said "0.272".
* The strength tag (强/弱/中性 + colour) is computed from the technicals in
  code, never by the LLM, so it is stable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from market_observer.domain.models import Briefing, OptionsSignal, SymbolSnapshot

DASH = "—"

# Discord embed colours (also used as a semantic key by the markdown renderer).
COLOR_STRONG = 0x2ECC71  # green
COLOR_WEAK = 0xE74C3C  # red
COLOR_NEUTRAL = 0xF1C40F  # amber
COLOR_INFO = 0x3498DB  # blue (overview/macro card)

HIGH_VOL_THRESHOLD = 60.0  # annualized realized vol % considered "high"
DEEP_INVERSION = -0.05  # term_structure <= this => "deep" backwardation


# --------------------------------------------------------------------------
# Number formatting
# --------------------------------------------------------------------------
def num(value: float | None, nd: int = 2) -> str:
    return DASH if value is None else f"{value:.{nd}f}"


def price(value: float | None) -> str:
    return DASH if value is None else f"{value:,.2f}"


def pct(value: float | None, nd: int = 1, signed: bool = False) -> str:
    if value is None:
        return DASH
    return f"{value:+.{nd}f}%" if signed else f"{value:.{nd}f}%"


def ratio(value: float | None, nd: int = 2) -> str:
    return DASH if value is None else f"{value:.{nd}f}"


def iv_pct(value: float | None, nd: int = 1) -> str:
    """Implied vol decimal (0.272) -> percent string (27.2%)."""
    return DASH if value is None else f"{value * 100:.{nd}f}%"


def term_label(o: OptionsSignal | None) -> str:
    """Human label for the IV term structure, no raw decimals."""
    if o is None or not o.has_data or o.term_structure is None:
        return DASH
    if o.term_structure_inverted:
        return "倒挂(深)" if o.term_structure <= DEEP_INVERSION else "倒挂"
    return "正常"


# --------------------------------------------------------------------------
# Strength tag (code-computed, not LLM)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalStyle:
    emoji: str
    label: str  # e.g. "强势" / "弱势 ⚠️高波动"
    color: int


def _trend_score(snap: SymbolSnapshot) -> int:
    t = snap.technicals
    score = 0
    for v in (t.price_vs_sma20_pct, t.price_vs_sma50_pct, t.price_vs_sma200_pct):
        if v is not None:
            score += 1 if v > 0 else -1
    if t.rsi_14 is not None:
        score += 1 if t.rsi_14 > 55 else (-1 if t.rsi_14 < 45 else 0)
    if t.macd_hist is not None:
        score += 1 if t.macd_hist > 0 else -1
    return score


def signal_style(snap: SymbolSnapshot) -> SignalStyle:
    """Map a snapshot's technicals to (emoji, label, colour)."""
    score = _trend_score(snap)
    if score >= 2:
        emoji, label, color = "🟢", "强势", COLOR_STRONG
    elif score <= -2:
        emoji, label, color = "🔴", "弱势", COLOR_WEAK
    else:
        emoji, label, color = "🟡", "中性", COLOR_NEUTRAL

    rv = snap.technicals.realized_vol_20
    if rv is not None and rv >= HIGH_VOL_THRESHOLD:
        label = f"{label} ⚠️高波动"
    return SignalStyle(emoji=emoji, label=label, color=color)


# --------------------------------------------------------------------------
# Today's highlights (data-driven, not LLM)
# --------------------------------------------------------------------------
def today_highlights(briefing: Briefing, limit: int = 5) -> list[str]:
    """Pick the most notable, *data-derived* facts of the day. No model calls,
    so this is always available even in a data-only briefing."""
    out: list[tuple[float, str]] = []  # (priority, text)

    # Macro shocks (VIX especially).
    for q in briefing.data.macro.quotes:
        if q.pct_change_1d is None:
            continue
        mag = abs(q.pct_change_1d)
        thresh = 8.0 if q.symbol == "^VIX" else 2.0
        if mag >= thresh:
            out.append((100 + mag, f"{q.name} {pct(q.pct_change_1d, signed=True)}（宏观异动）"))

    for snap in briefing.data.symbols:
        t = snap.technicals
        o = snap.options
        # Deep term-structure inversion = event pricing.
        if o is not None and o.has_data and o.term_structure is not None and o.term_structure_inverted:
            pr = 90 + abs(o.term_structure) * 100
            out.append(
                (pr, f"{snap.symbol} 期权期限{term_label(o)}（前端IV {iv_pct(o.front_atm_iv)}），市场在为近期事件定价")
            )
        # RSI extremes.
        if t.rsi_14 is not None and t.rsi_14 <= 30:
            out.append((80 + (30 - t.rsi_14), f"{snap.symbol} RSI {num(t.rsi_14, 0)}，深度超卖"))
        elif t.rsi_14 is not None and t.rsi_14 >= 70:
            out.append((80 + (t.rsi_14 - 70), f"{snap.symbol} RSI {num(t.rsi_14, 0)}，超买"))
        # Big recent moves.
        if snap.recent is not None and snap.recent.ret_5d_pct is not None:
            mag = abs(snap.recent.ret_5d_pct)
            if mag >= 8.0:
                out.append((60 + mag, f"{snap.symbol} 近5日 {pct(snap.recent.ret_5d_pct, signed=True)}"))
        # Imminent earnings.
        if snap.event is not None and snap.event.days_to_earnings is not None:
            d = snap.event.days_to_earnings
            if 0 <= d <= 5:
                out.append((70 + (5 - d), f"{snap.symbol} {d} 天后财报"))

    out.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    picked: list[str] = []
    for _pri, text in out:
        if text in seen:
            continue
        seen.add(text)
        picked.append(text)
        if len(picked) >= limit:
            break
    return picked
