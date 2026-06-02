"""T-08: deterministic orchestrator DAG."""

from __future__ import annotations

import json
from datetime import date, datetime

from market_observer.agents.llm_client import LLMError
from market_observer.agents.orchestrator import run_briefing
from market_observer.domain.models import (
    BriefingData,
    MacroContext,
    MacroQuote,
    SymbolSnapshot,
)

from .conftest import ScriptedLLM


def _data() -> BriefingData:
    return BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(
            as_of=date(2026, 6, 1),
            quotes=[MacroQuote(name="VIX", symbol="^VIX", value=14.0)],
        ),
        symbols=[SymbolSnapshot(symbol="AAPL", as_of=date(2026, 6, 1), last_price=200.0)],
    )


def _route(system: str, user: str, json_mode: bool) -> str:
    if "技术面分析师" in system:
        return json.dumps({"overall": "tech", "per_symbol": {"AAPL": "t"}})
    if "期权分析师" in system:
        return json.dumps({"overall": "opt", "per_symbol": {"AAPL": "o"}})
    if "宏观分析师" in system:
        return json.dumps({"overall": "macro"})
    if "主编" in system:
        return json.dumps({"overall_summary": "sum", "symbols": [{"symbol": "AAPL", "narrative": "n"}]})
    return "{}"


def test_happy_path_full_briefing() -> None:
    llm = ScriptedLLM(handler=_route)
    b = run_briefing(llm, _data(), now=datetime(2026, 6, 1, 8, 0))
    assert b.technical.ok and b.options.ok and b.macro_analysis.ok
    assert b.synthesis.ok
    assert b.synthesis.overall_summary == "sum"
    assert b.synthesis.symbols[0].narrative == "n"
    assert len(llm.calls) == 4  # 3 specialists + synthesizer, fixed


def test_dag_order_is_fixed() -> None:
    llm = ScriptedLLM(handler=_route)
    run_briefing(llm, _data())
    order = []
    for system, _user, _json in llm.calls:
        if "技术面分析师" in system:
            order.append("technical")
        elif "期权分析师" in system:
            order.append("options")
        elif "宏观分析师" in system:
            order.append("macro")
        elif "主编" in system:
            order.append("synthesizer")
    assert order == ["technical", "options", "macro", "synthesizer"]


def test_specialist_failure_does_not_abort() -> None:
    def route(system: str, user: str, json_mode: bool) -> str:
        if "期权分析师" in system:
            raise LLMError("options down")
        return _route(system, user, json_mode)

    llm = ScriptedLLM(handler=route)
    b = run_briefing(llm, _data())
    assert b.options.ok is False
    assert b.technical.ok is True
    assert b.macro_analysis.ok is True
    assert b.synthesis.ok is True  # synthesizer still runs


def test_synthesizer_failure_keeps_data_and_specialists() -> None:
    def route(system: str, user: str, json_mode: bool) -> str:
        if "主编" in system:
            raise LLMError("synth down")
        return _route(system, user, json_mode)

    llm = ScriptedLLM(handler=route)
    b = run_briefing(llm, _data())
    assert b.synthesis.ok is False
    assert b.technical.ok is True
    assert b.data.symbols[0].symbol == "AAPL"  # pure data intact


def test_all_llm_down_still_returns_briefing() -> None:
    llm = ScriptedLLM(raises=LLMError("everything down"))
    b = run_briefing(llm, _data())
    assert b.technical.ok is False
    assert b.options.ok is False
    assert b.macro_analysis.ok is False
    assert b.synthesis.ok is False
    assert b.data.symbols[0].symbol == "AAPL"
