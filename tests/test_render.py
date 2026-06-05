"""T-09: markdown rendering."""

from __future__ import annotations

from datetime import date, datetime

from market_observer.domain.models import (
    Briefing,
    BriefingData,
    MacroContext,
    MacroQuote,
    OptionsSignal,
    SpecialistOutput,
    SymbolNarrative,
    SymbolSnapshot,
    SynthesizerOutput,
    TechnicalIndicators,
)
from market_observer.render.markdown import render_briefing


def _briefing(*, synth_ok: bool = True, with_options: bool = True) -> Briefing:
    snap = SymbolSnapshot(
        symbol="AAPL",
        as_of=date(2026, 6, 1),
        last_price=200.0,
        technicals=TechnicalIndicators(rsi_14=55.0, macd_hist=0.5, price_vs_sma20_pct=1.2),
        options=(
            OptionsSignal(symbol="AAPL", has_data=True, front_atm_iv=0.25, iv_skew=0.03)
            if with_options
            else OptionsSignal(symbol="AAPL", has_data=False, note="option chain too thin")
        ),
    )
    data = BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(
            as_of=date(2026, 6, 1),
            quotes=[MacroQuote(name="VIX", symbol="^VIX", value=14.2, pct_change_1d=-2.0)],
        ),
        symbols=[snap],
    )
    synth = (
        SynthesizerOutput(
            ok=True,
            overall_summary="整体平稳。",
            symbols=[SymbolNarrative(symbol="AAPL", narrative="技术中性，期权偏防御。")],
        )
        if synth_ok
        else SynthesizerOutput.failed("down")
    )
    return Briefing(
        generated_at=datetime(2026, 6, 1, 8, 0),
        data=data,
        technical=SpecialistOutput(agent_name="technical", ok=True, per_symbol={"AAPL": "tech note"}),
        options=SpecialistOutput(agent_name="options", ok=True, per_symbol={"AAPL": "opt note"}),
        macro_analysis=SpecialistOutput(agent_name="macro", ok=True, overall="risk-on"),
        synthesis=synth,
    )


def test_render_contains_core_sections() -> None:
    md = render_briefing(_briefing())
    assert "# 盘前简报 · 2026-06-01" in md
    assert "## 全盘综述" in md
    assert "整体平稳。" in md
    assert "## 宏观快照" in md
    assert "VIX" in md
    assert "### AAPL" in md
    assert "技术中性，期权偏防御。" in md
    assert "RSI(14)" in md
    assert briefing_disclaimer() in md


def briefing_disclaimer() -> str:
    from market_observer.domain.models import DEFAULT_DISCLAIMER

    return DEFAULT_DISCLAIMER


def test_render_synth_failure_falls_back_to_specialists() -> None:
    md = render_briefing(_briefing(synth_ok=False))
    assert "综述不可用" in md
    # falls back to specialist per-symbol notes
    assert "技术面：tech note" in md
    assert "期权面：opt note" in md


def test_render_options_insufficient() -> None:
    md = render_briefing(_briefing(with_options=False))
    assert "option chain too thin" in md


def test_render_handles_all_none_numbers() -> None:
    data = BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(as_of=date(2026, 6, 1), quotes=[MacroQuote(name="VIX", symbol="^VIX")]),
        symbols=[SymbolSnapshot(symbol="ZZZZ", as_of=date(2026, 6, 1))],
    )
    b = Briefing(generated_at=datetime(2026, 6, 1, 8, 0), data=data)
    md = render_briefing(b)
    assert "—" in md  # missing numbers shown as dash
    assert "### ZZZZ" in md
