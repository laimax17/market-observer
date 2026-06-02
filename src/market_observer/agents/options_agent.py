"""Options analyst agent: interprets the EOD options signals for all symbols."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, options_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "options"

SYSTEM = """你是一名期权分析师。你只能基于下面提供的结构化期权信号做解读。
背景知识（仅供解读，不可外推未提供的数据）：
- term_structure 为负（front IV 高于 next）通常意味着市场在为临近事件定价。
- iv_skew 为正（OTM put IV 高于 OTM call IV）通常反映下行保护需求。
- put/call ratio 偏高通常代表防御/看跌情绪，但散户层面噪声大，不可单独当方向信号。
严格规则：
- 不得编造数据。has_data=false 的标的直接说明"期权数据不足"。
- 不得给出买/卖建议，不得输出方向+置信度数字。
- 全部用中文。
输出严格的 JSON：
{"overall": "对整个 watchlist 期权面的简短综述",
 "per_symbol": {"AAPL": "该标的期权信号的一两句解读", ...}}"""


def run(llm: LLMClient, data: BriefingData) -> SpecialistOutput:
    facts = [options_facts(s) for s in data.symbols]
    user = "以下是各标的的期权 EOD 信号：\n" + to_json(facts)
    try:
        raw = llm.complete(SYSTEM, user, json_mode=True)
        parsed = extract_json(raw)
    except (LLMError, ValueError) as exc:
        logger.warning("options agent failed: %s", exc)
        return SpecialistOutput.failed(NAME, str(exc))
    return SpecialistOutput(
        agent_name=NAME,
        ok=True,
        overall=str(parsed.get("overall", "")),
        per_symbol={str(k): str(v) for k, v in dict(parsed.get("per_symbol", {})).items()},
    )
