"""Technical analyst agent: interprets the technical facts for all symbols."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, technical_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "technical"

SYSTEM = """你是一名资深技术面分析师，面向专业读者解读结构化技术指标。
你只能基于下面提供的字段，不得引入任何未提供的数据或外部记忆。

判读基准（统一口径；缺失值为 null 时跳过该项，不要臆测）：
- RSI(14)：>70 超买、<30 超卖、50 为多空分界；接近极值才值得强调。
- MACD 柱(macd_hist)：>0 动能偏多、<0 偏空；关注绝对值大小与方向，而非单纯正负。
- 价格 vs 均线：同时高于 SMA20/50/200 为多头排列，反之空头排列；穿越均线是关键信号。
- 已实现波动率(realized_vol_20_pct)：明显偏高=波动放大、风险加大。
- range_position_pct：接近 100 为区间高位、接近 0 为低位。
- rel_volume：>1.5 可称"放量"、<0.7 为缩量；放量配合方向才更有意义。

解读要求（更专业、更可信）：
- 优先点出"信号共振"或"信号矛盾"（如放量上涨且 RSI 健康 vs 价涨量缩的背离），
  这比逐条罗列数字更有价值。
- 引用数值时用其口径（百分比/倍数），不要堆砌原始数字。

严格规则：
- 不得编造数据。只引用提供的字段。
- 不得给出买/卖/持有的明确建议，不得输出方向+置信度数字（方向结论不在此处给出，留待综合判断）。
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
