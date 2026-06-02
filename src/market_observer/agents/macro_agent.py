"""Macro analyst agent: interprets the day's macro backdrop (no per-symbol)."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, macro_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "macro"

SYSTEM = """你是一名宏观分析师。你只能基于下面提供的宏观指标读数做解读。
背景：VIX 反映波动率/避险情绪；美元指数、10Y 收益率、原油、黄金反映风险偏好与流动性环境。
严格规则：
- 不得编造数据。缺失值为 null 时不要臆测。
- 不得给出买/卖建议或置信度数字。
- 只描述当日宏观环境是偏 risk-on 还是 risk-off、有哪些值得留意的点。
- 全部用中文。
输出严格的 JSON：
{"overall": "对当日宏观环境的简短综述（3-5 句）"}"""


def run(llm: LLMClient, data: BriefingData) -> SpecialistOutput:
    user = "以下是当日宏观指标读数（pct_change_1d 为日涨跌幅%）：\n" + to_json(macro_facts(data))
    try:
        raw = llm.complete(SYSTEM, user, json_mode=True)
        parsed = extract_json(raw)
    except (LLMError, ValueError) as exc:
        logger.warning("macro agent failed: %s", exc)
        return SpecialistOutput.failed(NAME, str(exc))
    return SpecialistOutput(agent_name=NAME, ok=True, overall=str(parsed.get("overall", "")))
