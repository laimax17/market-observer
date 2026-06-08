"""Render a Briefing into Discord webhook *embeds* (coloured cards).

Discord does NOT render Markdown tables, so the push channel uses native
embeds instead: one overview card (今日看点 + 全盘综述 + 宏观) plus one card per
symbol with a colour stripe by strength, inline metric fields (laid out for
mobile's 2-column view), and the four-part narrative in the description.

Pure functions only. ``build_embeds`` returns a flat list of embed dicts;
``batch_embeds`` splits them into per-message batches respecting Discord's
limits (<=10 embeds and <=6000 characters per message). The notifier just
POSTs each batch.
"""

from __future__ import annotations

from typing import Any

from market_observer.domain.forecast import forecast_levels
from market_observer.domain.models import Briefing, SymbolNarrative, SymbolSnapshot

from .format import (
    COLOR_INFO,
    DASH,
    iv_pct,
    num,
    pct,
    price,
    ratio,
    signal_style,
    term_label,
    today_highlights,
)

MAX_EMBEDS_PER_MESSAGE = 10
MAX_CHARS_PER_MESSAGE = 6000
# Stay clear of Discord's hard caps.
TITLE_CAP = 240
DESC_CAP = 4000
FIELD_VALUE_CAP = 1000


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _field(name: str, value: str, inline: bool = True) -> dict[str, Any]:
    return {"name": name or DASH, "value": _clip(value or DASH, FIELD_VALUE_CAP), "inline": inline}


def _narrative_for(briefing: Briefing, symbol: str) -> SymbolNarrative | None:
    synth = briefing.synthesis
    if synth is not None and synth.ok:
        for sn in synth.symbols:
            if sn.symbol == symbol:
                return sn
    return None


def _narrative_text(briefing: Briefing, symbol: str) -> str:
    sn = _narrative_for(briefing, symbol)
    if sn is not None and sn.has_structured:
        parts = []
        if sn.recent:
            parts.append(f"📌 **近况** {sn.recent}")
        if sn.why:
            parts.append(f"🔍 **归因** {sn.why}")
        if sn.forecast:
            parts.append(f"🔭 **预测** {sn.forecast}")
        return "\n\n".join(parts)
    if sn is not None and sn.narrative:
        return sn.narrative
    notes: list[str] = []
    for spec, label in ((briefing.technical, "技术面"), (briefing.options, "期权面")):
        if spec is not None and spec.ok and symbol in spec.per_symbol:
            notes.append(f"**{label}** {spec.per_symbol[symbol]}")
    return "\n\n".join(notes) if notes else "（暂无解读）"


def _levels_value(snap: SymbolSnapshot) -> str:
    lv = forecast_levels(snap)
    implied = (
        f"{price(lv['implied_low'])}–{price(lv['implied_high'])}"
        if lv["implied_low"] is not None
        else DASH
    )
    smas = f"{num(lv['sma20'], 0)} / {num(lv['sma50'], 0)} / {num(lv['sma200'], 0)}"
    return f"隐含区间 {implied}\nSMA20/50/200 {smas}"


def _symbol_embed(briefing: Briefing, snap: SymbolSnapshot) -> dict[str, Any]:
    style = signal_style(snap)
    t = snap.technicals
    o = snap.options
    r = snap.recent

    fields = [
        _field("RSI(14)", num(t.rsi_14, 1)),
        _field("MACD柱", num(t.macd_hist)),
        _field("vs SMA20", pct(t.price_vs_sma20_pct, signed=True)),
        _field("vs SMA200", pct(t.price_vs_sma200_pct, signed=True)),
        _field("已实现波动率", pct(t.realized_vol_20)),
        _field("近5日", pct(r.ret_5d_pct, signed=True) if r else DASH),
    ]
    if o is not None and o.has_data:
        fields += [
            _field("近月ATM IV", iv_pct(o.front_atm_iv)),
            _field("期限结构", term_label(o)),
            _field("PCR(量)", ratio(o.put_call_volume_ratio)),
            _field("隐含波动(到期)", pct(o.implied_move_pct)),
        ]
    else:
        note = (o.note if o else None) or "数据不足"
        fields.append(_field("期权信号", note))
    fields.append(_field("关键价位", _levels_value(snap), inline=False))

    return {
        "title": _clip(f"{style.emoji} {snap.symbol} · {price(snap.last_price)}  {style.label}", TITLE_CAP),
        "color": style.color,
        "description": _clip(_narrative_text(briefing, snap.symbol), DESC_CAP),
        "fields": fields,
    }


def _overview_embed(briefing: Briefing) -> dict[str, Any]:
    as_of = briefing.data.as_of.isoformat()
    parts: list[str] = []

    highlights = today_highlights(briefing)
    if highlights:
        parts.append("**今日看点**\n" + "\n".join(f"• {h}" for h in highlights))

    synth = briefing.synthesis
    if synth is not None and synth.ok and synth.overall_summary:
        parts.append("**全盘综述**\n" + synth.overall_summary)

    macro = briefing.macro_analysis
    if macro is not None and macro.ok and macro.overall:
        parts.append("**宏观**\n" + macro.overall)

    fields = [
        _field(q.name, f"{num(q.value)}  ({pct(q.pct_change_1d, signed=True)})")
        for q in briefing.data.macro.quotes
    ]

    return {
        "title": f"📊 盘前简报 · {as_of}",
        "color": COLOR_INFO,
        "description": _clip("\n\n".join(parts) or "（数据简报）", DESC_CAP),
        "fields": fields,
        "footer": {"text": _clip(briefing.disclaimer, 2000)},
    }


def build_embeds(briefing: Briefing) -> list[dict[str, Any]]:
    embeds = [_overview_embed(briefing)]
    embeds += [_symbol_embed(briefing, s) for s in briefing.data.symbols]
    return embeds


def embed_char_count(embed: dict[str, Any]) -> int:
    total = len(str(embed.get("title", ""))) + len(str(embed.get("description", "")))
    for f in embed.get("fields", []):
        total += len(str(f.get("name", ""))) + len(str(f.get("value", "")))
    footer = embed.get("footer")
    if isinstance(footer, dict):
        total += len(str(footer.get("text", "")))
    return total


def batch_embeds(
    embeds: list[dict[str, Any]],
    max_per_message: int = MAX_EMBEDS_PER_MESSAGE,
    max_chars: int = MAX_CHARS_PER_MESSAGE,
) -> list[list[dict[str, Any]]]:
    """Split embeds into messages of <=max_per_message and <=max_chars."""
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_chars = 0
    for e in embeds:
        c = embed_char_count(e)
        if cur and (len(cur) >= max_per_message or cur_chars + c > max_chars):
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(e)
        cur_chars += c
    if cur:
        batches.append(cur)
    return batches


def render_embed_messages(briefing: Briefing) -> list[list[dict[str, Any]]]:
    """Convenience: build + batch in one call. Each element is the ``embeds``
    array for one webhook message."""
    return batch_embeds(build_embeds(briefing))
