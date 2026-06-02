"""T-08: individual specialist + synthesizer agents."""

from __future__ import annotations

import json
from datetime import date

from market_observer.agents import (
    macro_agent,
    options_agent,
    synthesizer,
    technical_agent,
)
from market_observer.domain.models import (
    BriefingData,
    MacroContext,
    MacroQuote,
    OptionsSignal,
    SpecialistOutput,
    SymbolSnapshot,
    TechnicalIndicators,
)

from .conftest import ScriptedLLM


def _data() -> BriefingData:
    return BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(
            as_of=date(2026, 6, 1),
            quotes=[MacroQuote(name="VIX", symbol="^VIX", value=14.0, pct_change_1d=-2.0)],
        ),
        symbols=[
            SymbolSnapshot(
                symbol="AAPL",
                as_of=date(2026, 6, 1),
                last_price=200.0,
                technicals=TechnicalIndicators(rsi_14=55.0, macd_hist=0.5),
                options=OptionsSignal(symbol="AAPL", has_data=True, iv_skew=0.03),
            ),
        ],
    )


def test_technical_agent_ok() -> None:
    llm = ScriptedLLM(
        responses=[json.dumps({"overall": "稳", "per_symbol": {"AAPL": "中性"}})]
    )
    out = technical_agent.run(llm, _data())
    assert out.ok is True
    assert out.overall == "稳"
    assert out.per_symbol["AAPL"] == "中性"


def test_options_agent_ok() -> None:
    llm = ScriptedLLM(responses=[json.dumps({"overall": "偏防御", "per_symbol": {"AAPL": "skew 正"}})])
    out = options_agent.run(llm, _data())
    assert out.ok is True and out.per_symbol["AAPL"] == "skew 正"


def test_macro_agent_ok() -> None:
    llm = ScriptedLLM(responses=[json.dumps({"overall": "risk-on"})])
    out = macro_agent.run(llm, _data())
    assert out.ok is True and out.overall == "risk-on"


def test_agent_handles_malformed_json() -> None:
    llm = ScriptedLLM(responses=["这不是 JSON"])
    out = technical_agent.run(llm, _data())
    assert out.ok is False
    assert out.error is not None


def test_agent_tolerates_code_fences() -> None:
    fenced = "```json\n" + json.dumps({"overall": "x", "per_symbol": {}}) + "\n```"
    llm = ScriptedLLM(responses=[fenced])
    out = technical_agent.run(llm, _data())
    assert out.ok is True and out.overall == "x"


def test_synthesizer_ok() -> None:
    llm = ScriptedLLM(
        responses=[
            json.dumps(
                {
                    "overall_summary": "整体平稳",
                    "symbols": [{"symbol": "AAPL", "narrative": "技术中性，期权偏防御"}],
                }
            )
        ]
    )
    tech = SpecialistOutput(agent_name="technical", ok=True, overall="t")
    opt = SpecialistOutput(agent_name="options", ok=True, overall="o")
    mac = SpecialistOutput(agent_name="macro", ok=True, overall="m")
    out = synthesizer.run(llm, _data(), tech, opt, mac)
    assert out.ok is True
    assert out.overall_summary == "整体平稳"
    assert out.symbols[0].symbol == "AAPL"


def test_synthesizer_handles_missing_specialist() -> None:
    llm = ScriptedLLM(responses=[json.dumps({"overall_summary": "s", "symbols": []})])
    out = synthesizer.run(llm, _data(), None, None, None)
    assert out.ok is True
