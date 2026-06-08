"""Synthesizer (editor) agent: merges the three specialists into the briefing.

Produces a watchlist-level summary plus one narrative per symbol, weaving the
technical / options / macro perspectives together.
"""

from __future__ import annotations

import logging

from market_observer.domain.models import (
    BriefingData,
    SpecialistOutput,
    SymbolNarrative,
    SynthesizerOutput,
)

from .base import extract_json, synthesis_symbol_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "synthesizer"

SYSTEM = """你是这份盘前简报的主编，面向专业读者。下面给你三位分析师（技术面/期权/宏观）的解读，
以及每只标的的近期价量、可用新闻标题、即将到来的事件和代码算出的关键价位。
你要为每只标的写出结构化的四部分解读，并写一段全盘综述。

== 新闻使用铁律（最重要，违反等同于编造，会摧毁整篇简报的可信度）==
- news 里的标题是你做因果归因唯一可用的事实来源。你只能复述/转译标题中"明确写出"的内容。
- 严禁脑补标题里没有出现的任何具体信息：人名、机构/券商名、研报名称、具体数字或金额、
  以及并购/裁员/CEO 售股/诉讼等具体事件。
  反例（禁止）：标题只说"股价年内涨 270%"，你却写"CEO 大规模售股引发抛售"——这是编造。
  反例（禁止）：标题里没有"Oppenheimer""1.6 万亿"，你却写"Oppenheimer 报告警告 1.6 万亿电信行业"。
- 相关性门槛：若一条标题主要是在讲别的标的（标题点名的是其它股票/主题），对本标的视为"不相关"，
  不得当作本标的的催化剂引用。
- 自检：写完 why 后逐句检查——其中每一个因果点，是否都能在某条提供的标题里逐字找到依据？
  找不到依据的，必须删除，或改写为"无明确公开催化剂"。

每只标的输出四个字段：
1) recent（近况）：最近发生了什么。只能用提供的 recent 价量数据和（通过相关性门槛的）news 标题；
   若无相关新闻，就只描述价量（如"近5日下跌X%、放量跌破20日线"）。
2) why（归因）：为什么会这样。严格遵守上面的"新闻使用铁律"。
   若无相关新闻，必须明说"无明确公开催化剂，以下为基于价量与期权信号的推断"，再给推断。
3) forecast（预测）：先给出 1-2 条支撑该判断的证据（引用具体的技术/期权/价位事实，如
   "RSI 44 偏弱、跌破 20 日线、期权隐含 1σ 下沿在 198.6"），再给明确方向（偏多/偏空/中性）
   + 粗略概率（如"约六成"）+ 目标价区间 + 关键支撑/阻力 + 主要催化剂。
   目标价区间必须锚定提供的 levels（implied_low/implied_high 为期权隐含1σ区间，
   atr_low/atr_high 为ATR区间，sma20/50/200 为均线），不得凭空捏造价位。
   概率是模型粗略估计、非回测命中率。句末标注"（模型估计，非投资建议）"。
4) （可选）narrative：可留空。

严格规则：
- 不得编造数据或新闻。只能用提供给你的字段。
- 所有隐含波动率、收益率等一律用百分比表述（如27.2%），不要用0.272这种小数。
- 全部用中文，简洁，面向人类读者。
输出严格的 JSON：
{"overall_summary": "结合宏观与各标的的全盘综述（一段）",
 "symbols": [{"symbol": "AAPL", "recent": "...", "why": "...", "forecast": "..."}, ...]}"""


def _specialist_block(label: str, out: SpecialistOutput | None) -> dict[str, object]:
    if out is None or not out.ok:
        return {"label": label, "available": False}
    return {
        "label": label,
        "available": True,
        "overall": out.overall,
        "per_symbol": out.per_symbol,
    }


def run(
    llm: LLMClient,
    data: BriefingData,
    technical: SpecialistOutput | None,
    options: SpecialistOutput | None,
    macro: SpecialistOutput | None,
) -> SynthesizerOutput:
    payload = {
        "specialists": {
            "technical": _specialist_block("技术面", technical),
            "options": _specialist_block("期权面", options),
            "macro": _specialist_block("宏观", macro),
        },
        "per_symbol_facts": [synthesis_symbol_facts(s) for s in data.symbols],
    }
    user = "分析师解读、各标的近况/新闻/关键价位如下：\n" + to_json(payload)
    try:
        raw = llm.complete(SYSTEM, user, json_mode=True)
        parsed = extract_json(raw)
    except (LLMError, ValueError) as exc:
        logger.warning("synthesizer failed: %s", exc)
        return SynthesizerOutput.failed(str(exc))

    narratives: list[SymbolNarrative] = []
    for item in parsed.get("symbols", []):
        if not isinstance(item, dict) or "symbol" not in item:
            continue
        narratives.append(
            SymbolNarrative(
                symbol=str(item["symbol"]),
                recent=str(item.get("recent", "")),
                why=str(item.get("why", "")),
                forecast=str(item.get("forecast", "")),
                narrative=str(item.get("narrative", "")),
            )
        )
    return SynthesizerOutput(
        ok=True,
        overall_summary=str(parsed.get("overall_summary", "")),
        symbols=narratives,
    )
