"""Technical analyst agent: interprets the technical facts for all symbols."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, technical_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "technical"

SYSTEM = """你是一名技术面分析师。你只能基于下面提供的结构化技术指标做解读。
严格规则：
- 不得编造数据。只引用提供的字段。
- 不得给出买/卖/持有的明确建议，不得输出方向+置信度数字。
- 只描述：看到了什么信号、通常如何理解、需要留意什么。
- 全部用中文。
输出严格的 JSON，格式：
{"overall": "对整个 watchlist 技术面的简短综述",
 "per_symbol": {"AAPL": "该标的技术面的一两句解读", ...}}"""


def run(llm: LLMClient, data: BriefingData) -> SpecialistOutput:
    facts = [technical_facts(s) for s in data.symbols]
    user = "以下是各标的的技术指标（缺失值为 null，表示数据不足）：\n" + to_json(facts)
    try:
        raw = llm.complete(SYSTEM, user, json_mode=True)
        parsed = extract_json(raw)
    except (LLMError, ValueError) as exc:
        logger.warning("technical agent failed: %s", exc)
        return SpecialistOutput.failed(NAME, str(exc))
    return SpecialistOutput(
        agent_name=NAME,
        ok=True,
        overall=str(parsed.get("overall", "")),
        per_symbol={str(k): str(v) for k, v in dict(parsed.get("per_symbol", {})).items()},
    )
