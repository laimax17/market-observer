"""Options analyst agent: interprets the EOD options signals for all symbols."""

from __future__ import annotations

import logging

from market_observer.domain.models import BriefingData, SpecialistOutput

from .base import extract_json, options_facts, to_json
from .llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

NAME = "options"

SYSTEM = """你是一名资深期权分析师，面向专业读者解读 EOD 期权信号。
你只能基于下面提供的字段，不得外推任何未提供的数据。

判读基准（仅供解读，缺失值为 null 时跳过）：
- term_structure 为负 / term_structure_inverted=true（front IV 高于 next）：通常是市场在为
  临近的离散事件（多为财报）定价。若 days_to_earnings 临近（如 ≤14 天），应优先点明
  "倒挂主因财报临近（约 X 天后）"，而不要泛泛说"近期事件"。
- front_atm_iv 相对 realized_vol_20_pct：IV 明显高于已实现波动 = 期权偏贵（市场预期波动放大
  或含事件溢价）；接近或低于 = 相对便宜。这是判断"贵不贵"的关键，请显式比较。
- iv_skew 为正（OTM put IV 高于 call）：反映下行保护需求/避险；为负多见于追涨情绪。
- put/call：volume 反映当日情绪（噪声大、不可单独当方向信号）；oi 反映存量持仓、更稳定。
  两者方向背离时值得一提。
- implied_move_pct：到期前的 1σ 预期波动幅度，用于量化"市场预期波动有多大"。

严格规则：
- 不得编造数据。has_data=false 的标的直接说明"期权数据不足"。
- 你只做客观信号描述，不下方向结论（方向结论不在此处给出，留待综合判断）。
- 引用隐含波动率(IV)时一律换算成百分比表述（如 0.272 写成 27.2%）。
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
