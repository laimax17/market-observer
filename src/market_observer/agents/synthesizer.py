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

SYSTEM = """你是这份盘前简报的主编。下面给你三位分析师（技术面/期权/宏观）的解读，
以及每只标的的近期价量、可用新闻标题和代码算出的关键价位。
你要为每只标的写出结构化的四部分解读，并写一段全盘综述。

每只标的输出四个字段：
1) recent（近况）：最近发生了什么。只能用提供的 recent 价量数据和 news 标题；
   若没有新闻，就只描述价量（如"近5日下跌X%、放量跌破20日线"）。
2) why（归因）：为什么会这样。只能引用提供的 news 标题做因果归因；
   若没有相关新闻，必须明说"无明确公开催化剂，以下为基于价量与期权信号的推断"，再给推断。
3) forecast（预测）：给出明确方向（偏多/偏空/中性）+ 粗略概率（如"约六成"）
   + 目标价区间 + 关键价位 + 主要催化剂。
   目标价区间必须锚定提供的 levels（implied_low/implied_high 为期权隐含1σ区间，
   atr_low/atr_high 为ATR区间，sma20/50/200 为均线），不得凭空捏造价位。
   句末标注"（模型估计，非投资建议）"。
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
