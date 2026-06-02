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

from .base import extract_json, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "synthesizer"

SYSTEM = """你是这份盘前简报的主编。下面给你三位分析师（技术面/期权/宏观）的解读。
你的任务：把它们整合成一份给人阅读的简报。
严格规则：
- 只能使用三位分析师提供的解读，不得引入新数据或新指标。
- 不得给出买/卖建议，不得输出方向+置信度数字。
- 语言简洁、面向人类读者，帮助他自己判断，而不是替他决定。
- 全部用中文。
输出严格的 JSON：
{"overall_summary": "结合宏观与各标的的全盘综述（一段）",
 "symbols": [{"symbol": "AAPL", "narrative": "整合技术面与期权面的一段解读"}, ...]}"""


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
        "symbols": [s.symbol for s in data.symbols],
        "technical": _specialist_block("技术面", technical),
        "options": _specialist_block("期权面", options),
        "macro": _specialist_block("宏观", macro),
    }
    user = "三位分析师的解读如下：\n" + to_json(payload)
    try:
        raw = llm.complete(SYSTEM, user, json_mode=True)
        parsed = extract_json(raw)
    except (LLMError, ValueError) as exc:
        logger.warning("synthesizer failed: %s", exc)
        return SynthesizerOutput.failed(str(exc))

    narratives: list[SymbolNarrative] = []
    for item in parsed.get("symbols", []):
        try:
            narratives.append(
                SymbolNarrative(symbol=str(item["symbol"]), narrative=str(item.get("narrative", "")))
            )
        except (KeyError, TypeError):
            continue
    return SynthesizerOutput(
        ok=True,
        overall_summary=str(parsed.get("overall_summary", "")),
        symbols=narratives,
    )
