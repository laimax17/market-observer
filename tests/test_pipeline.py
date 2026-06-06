"""T-11: end-to-end pipeline over fakes (FakeProvider + ScriptedLLM)."""

from __future__ import annotations

import json
from datetime import date, datetime

from market_observer.domain.options_math import ExpiryChain, OptionQuote
from market_observer.pipeline import assemble_briefing_data, generate_briefing

from .conftest import FakeProvider, ScriptedLLM, make_ohlcv


def _provider() -> FakeProvider:
    exp = date(2026, 6, 19)
    chain = ExpiryChain(
        expiry=exp,
        calls=[
            OptionQuote(strike=200.0, implied_vol=0.25, volume=100, open_interest=500),
            OptionQuote(strike=210.0, implied_vol=0.22, volume=80, open_interest=300),
        ],
        puts=[
            OptionQuote(strike=200.0, implied_vol=0.27, volume=120, open_interest=600),
            OptionQuote(strike=190.0, implied_vol=0.30, volume=90, open_interest=250),
        ],
    )
    return FakeProvider(
        histories={"AAPL": make_ohlcv(), "MSFT": make_ohlcv(start=300.0)},
        universe=["AAPL", "MSFT", "NVDA"],
        avg_volumes={"AAPL": 9e7, "MSFT": 8e7, "NVDA": 7e7},
        expiries={"AAPL": [exp], "MSFT": [exp]},
        chains={("AAPL", exp): chain, ("MSFT", exp): chain},
        macro={"^VIX": (14.2, -2.0), "^TNX": (4.3, 0.5)},
    )


def _route(system: str, user: str, json_mode: bool) -> str:
    if "技术面分析师" in system:
        return json.dumps({"overall": "tech", "per_symbol": {"AAPL": "t", "MSFT": "t"}})
    if "期权分析师" in system:
        return json.dumps({"overall": "opt", "per_symbol": {"AAPL": "o", "MSFT": "o"}})
    if "宏观分析师" in system:
        return json.dumps({"overall": "macro"})
    if "主编" in system:
        return json.dumps(
            {
                "overall_summary": "sum",
                "symbols": [
                    {"symbol": "AAPL", "narrative": "n1"},
                    {"symbol": "MSFT", "narrative": "n2"},
                ],
            }
        )
    return "{}"


def test_assemble_briefing_data_populates_snapshots() -> None:
    data = assemble_briefing_data(_provider(), ["AAPL", "MSFT"], date(2026, 6, 1))
    assert [s.symbol for s in data.symbols] == ["AAPL", "MSFT"]
    assert data.symbols[0].last_price is not None
    assert data.symbols[0].options is not None
    assert data.macro.quotes  # macro filled


def test_generate_briefing_full_pipeline() -> None:
    llm = ScriptedLLM(handler=_route)
    b = generate_briefing(
        _provider(),
        llm,
        pinned=["AAPL"],
        size=2,
        as_of=date(2026, 6, 1),
        now=datetime(2026, 6, 1, 8, 0),
    )
    assert b.synthesis.ok
    assert b.synthesis.overall_summary == "sum"
    assert len(llm.calls) == 4  # fixed: 3 specialists + synthesizer
    assert "AAPL" in [s.symbol for s in b.data.symbols]


def test_generate_briefing_data_only_when_no_llm() -> None:
    b = generate_briefing(
        _provider(),
        None,
        pinned=["AAPL"],
        size=2,
        as_of=date(2026, 6, 1),
        now=datetime(2026, 6, 1, 8, 0),
    )
    assert b.synthesis is None
    assert b.technical is None
    assert b.data.symbols  # pure data still present
