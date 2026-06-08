"""Render a Briefing to Markdown (the GitHub/file archive).

Tables are right-aligned on numeric columns with units in the header, grouped
by theme. Each symbol leads with a code-computed strength tag, then a polished
technical + options table, a data-grounded key-levels line, and the four-part
narrative (近况 / 归因 / 预测). Degrades gracefully: missing numbers show "—";
if the synthesizer failed, per-symbol notes fall back to the specialists.
Nothing is fabricated.
"""

from __future__ import annotations

from market_observer.domain.forecast import forecast_levels
from market_observer.domain.models import Briefing, SymbolNarrative, SymbolSnapshot

from .format import (
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


def _highlights_section(briefing: Briefing) -> list[str]:
    items = today_highlights(briefing)
    if not items:
        return []
    return ["## 今日看点", "", *[f"- {it}" for it in items], ""]


def _macro_section(briefing: Briefing) -> list[str]:
    lines = ["## 宏观快照", "", "| 指标 | 数值 | 日涨跌 |", "| :-- | --: | --: |"]
    for q in briefing.data.macro.quotes:
        lines.append(f"| {q.name} | {num(q.value)} | {pct(q.pct_change_1d, signed=True)} |")
    macro = briefing.macro_analysis
    if macro is not None and macro.ok and macro.overall:
        lines += ["", f"> {macro.overall}"]
    elif macro is not None and not macro.ok:
        lines += ["", "> （宏观分析不可用）"]
    return lines


def _technical_table(snap: SymbolSnapshot) -> list[str]:
    t = snap.technicals
    r = snap.recent
    ret5 = pct(r.ret_5d_pct, signed=True) if r else DASH
    ret20 = pct(r.ret_20d_pct, signed=True) if r else DASH
    return [
        "**技术面**",
        "",
        "| 趋势 | 值 | 动量 / 波动 | 值 |",
        "| :-- | --: | :-- | --: |",
        f"| vs SMA20 | {pct(t.price_vs_sma20_pct, signed=True)} | RSI(14) | {num(t.rsi_14, 1)} |",
        f"| vs SMA50 | {pct(t.price_vs_sma50_pct, signed=True)} | MACD柱 | {num(t.macd_hist)} |",
        f"| vs SMA200 | {pct(t.price_vs_sma200_pct, signed=True)} | 已实现波动率 | {pct(t.realized_vol_20)} |",
        f"| 区间位置 | {pct(t.range_position_pct)} | ATR(14) | {num(t.atr_14)} |",
        f"| 近5日 | {ret5} | 近20日 | {ret20} |",
    ]


def _options_table(snap: SymbolSnapshot) -> list[str]:
    o = snap.options
    if o is None or not o.has_data:
        note = (o.note if o else None) or "数据不足"
        return ["**期权面**", "", f"_期权信号：{note}_"]
    return [
        "**期权面**",
        "",
        "| 指标 | 值 | 指标 | 值 |",
        "| :-- | --: | :-- | --: |",
        f"| 近月ATM IV | {iv_pct(o.front_atm_iv)} | 期限结构 | {term_label(o)} |",
        f"| 次月ATM IV | {iv_pct(o.next_atm_iv)} | PCR(量) | {ratio(o.put_call_volume_ratio)} |",
        f"| IV skew | {iv_pct(o.iv_skew)} | 隐含波动(到期) | {pct(o.implied_move_pct)} |",
    ]


def _levels_line(snap: SymbolSnapshot) -> str:
    lv = forecast_levels(snap)
    implied = (
        f"{price(lv['implied_low'])}–{price(lv['implied_high'])}"
        if lv["implied_low"] is not None
        else DASH
    )
    atr = (
        f"{price(lv['atr_low'])}–{price(lv['atr_high'])}" if lv["atr_low"] is not None else DASH
    )
    smas = f"{num(lv['sma20'], 0)} / {num(lv['sma50'], 0)} / {num(lv['sma200'], 0)}"
    return (
        f"**关键价位**：现价 {price(lv['last'])} ｜ 隐含区间 {implied} ｜ "
        f"ATR区间 {atr} ｜ SMA20/50/200 {smas}"
    )


def _news_lines(snap: SymbolSnapshot, limit: int = 3) -> list[str]:
    if not snap.news:
        return []
    out = ["**近期新闻**"]
    for n in snap.news[:limit]:
        d = f"{n.published.isoformat()} · " if n.published else ""
        out.append(f"- {d}{n.title}")
    return out


def _narrative_for(briefing: Briefing, symbol: str) -> SymbolNarrative | None:
    synth = briefing.synthesis
    if synth is not None and synth.ok:
        for sn in synth.symbols:
            if sn.symbol == symbol:
                return sn
    return None


def _narrative_lines(briefing: Briefing, symbol: str) -> list[str]:
    sn = _narrative_for(briefing, symbol)
    if sn is not None and sn.has_structured:
        out: list[str] = []
        if sn.recent:
            out.append(f"**📌 近况**：{sn.recent}")
        if sn.why:
            out.append(f"**🔍 归因**：{sn.why}")
        if sn.forecast:
            out.append(f"**🔭 预测**：{sn.forecast}")
        return out
    if sn is not None and sn.narrative:
        return [sn.narrative]
    # Fallback: stitch specialist per-symbol notes.
    notes: list[str] = []
    for spec, label in ((briefing.technical, "技术面"), (briefing.options, "期权面")):
        if spec is not None and spec.ok and symbol in spec.per_symbol:
            notes.append(f"{label}：{spec.per_symbol[symbol]}")
    return ["；".join(notes) if notes else "（暂无解读）"]


def _symbol_section(briefing: Briefing, snap: SymbolSnapshot) -> list[str]:
    style = signal_style(snap)
    lines = [f"### {style.emoji} {snap.symbol} · {price(snap.last_price)} （{style.label}）", ""]
    lines += _technical_table(snap)
    lines += [""]
    lines += _options_table(snap)
    lines += ["", _levels_line(snap)]
    news = _news_lines(snap)
    if news:
        lines += ["", *news]
    lines += [""]
    lines += _narrative_lines(briefing, snap.symbol)
    return lines


def _overall(briefing: Briefing) -> list[str]:
    synth = briefing.synthesis
    if synth is not None and synth.ok and synth.overall_summary:
        return ["## 全盘综述", "", synth.overall_summary]
    return ["## 全盘综述", "", "_（综述不可用，请直接看下方各标的数据与解读。）_"]


def render_briefing(briefing: Briefing) -> str:
    ts = briefing.generated_at.strftime("%Y-%m-%d %H:%M")
    as_of = briefing.data.as_of.isoformat()
    lines: list[str] = [
        f"# 盘前简报 · {as_of}",
        "",
        f"_生成时间：{ts}_",
        "",
    ]
    lines += _highlights_section(briefing)
    lines += _overall(briefing)
    lines += [""]
    lines += _macro_section(briefing)
    lines += ["", "## 标的观察", ""]
    for snap in briefing.data.symbols:
        lines += _symbol_section(briefing, snap)
        lines += [""]
    lines += ["---", "", f"_{briefing.disclaimer}_"]
    return "\n".join(lines)
