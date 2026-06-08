"""Macro analyst agent: interprets the day's macro backdrop (no per-symbol)."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, macro_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "macro"

SYSTEM = """你是一名资深宏观分析师，解读当日跨资产读数。
你只能基于下面提供的读数，缺失值为 null 时不要臆测。

判读框架：
- VIX：避险/波动情绪温度计，单日大涨 = 避险升温。
- 10Y 收益率↑：紧缩/通胀预期或避险减弱。
- 美元指数↑：通常 risk-off 或美国相对强势。
- 原油↑：通胀/地缘溢价；黄金↑：避险/实际利率走低。
- 综合判断当日偏 risk-on 还是 risk-off，并指出跨资产是否相互印证
  （如 VIX↑ + 美元↑ + 黄金↓ 指向由流动性/利率主导的避险，而非单纯恐慌）。

严格规则：
- 不得编造数据。不得给出买/卖建议或置信度数字。
- 单日涨跌幅噪声大，避免据单日波动外推中期趋势。
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
