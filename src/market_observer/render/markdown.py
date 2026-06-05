"""Render a Briefing to Markdown.

Degrades gracefully: missing numbers show as "—"; if the synthesizer failed,
per-symbol narratives fall back to the specialists' notes; if a specialist
failed, its section is omitted with a short note. Nothing is fabricated.
"""

from __future__ import annotations

from market_observer.domain.models import Briefing, SymbolSnapshot


def _num(value: float | None, nd: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{nd}f}{suffix}"


def _macro_section(briefing: Briefing) -> list[str]:
    lines = ["## 宏观快照", "", "| 指标 | 数值 | 日涨跌 |", "| --- | --- | --- |"]
    for q in briefing.data.macro.quotes:
        lines.append(f"| {q.name} | {_num(q.value)} | {_num(q.pct_change_1d, suffix='%')} |")
    macro = briefing.macro_analysis
    if macro is not None and macro.ok and macro.overall:
        lines += ["", f"> {macro.overall}"]
    elif macro is not None and not macro.ok:
        lines += ["", "> （宏观分析不可用）"]
    return lines


def _technical_table(snap: SymbolSnapshot) -> list[str]:
    t = snap.technicals
    return [
        "| 指标 | 值 | 指标 | 值 |",
        "| --- | --- | --- | --- |",
        f"| 现价 | {_num(snap.last_price)} | RSI(14) | {_num(t.rsi_14)} |",
        f"| vs SMA20 | {_num(t.price_vs_sma20_pct, suffix='%')} | "
        f"vs SMA50 | {_num(t.price_vs_sma50_pct, suffix='%')} |",
        f"| vs SMA200 | {_num(t.price_vs_sma200_pct, suffix='%')} | "
        f"MACD柱 | {_num(t.macd_hist, 4)} |",
        f"| 已实现波动率 | {_num(t.realized_vol_20, suffix='%')} | "
        f"ATR(14) | {_num(t.atr_14)} |",
        f"| 区间位置 | {_num(t.range_position_pct, suffix='%')} | "
        f"相对成交量 | {_num(t.rel_volume)} |",
    ]


def _options_line(snap: SymbolSnapshot) -> str:
    o = snap.options
    if o is None or not o.has_data:
        note = (o.note if o else None) or "数据不足"
        return f"**期权信号**：{note}"
    parts = [
        f"近月ATM IV {_num(o.front_atm_iv, 4)}",
        f"次月ATM IV {_num(o.next_atm_iv, 4)}",
        f"期限结构 {_num(o.term_structure, 4)}"
        + ("（倒挂）" if o.term_structure_inverted else ""),
        f"PCR(量) {_num(o.put_call_volume_ratio)}",
        f"IV skew {_num(o.iv_skew, 4)}",
    ]
    return "**期权信号**：" + " ｜ ".join(parts)


def _narrative_for(briefing: Briefing, symbol: str) -> str:
    synth = briefing.synthesis
    if synth is not None and synth.ok:
        for sn in synth.symbols:
            if sn.symbol == symbol:
                return sn.narrative
    # Fallback: stitch specialist per-symbol notes.
    notes: list[str] = []
    for spec, label in (
        (briefing.technical, "技术面"),
        (briefing.options, "期权面"),
    ):
        if spec is not None and spec.ok and symbol in spec.per_symbol:
            notes.append(f"{label}：{spec.per_symbol[symbol]}")
    return "；".join(notes) if notes else "（暂无解读）"


def _symbol_section(briefing: Briefing, snap: SymbolSnapshot) -> list[str]:
    lines = [f"### {snap.symbol}", ""]
    lines += _technical_table(snap)
    lines += ["", _options_line(snap), "", _narrative_for(briefing, snap.symbol)]
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
    lines += _overall(briefing)
    lines += [""]
    lines += _macro_section(briefing)
    lines += ["", "## 标的观察", ""]
    for snap in briefing.data.symbols:
        lines += _symbol_section(briefing, snap)
        lines += [""]
    lines += ["---", "", f"_{briefing.disclaimer}_"]
    return "\n".join(lines)
