"""T-02: domain model validation and serialization round-trips."""

from __future__ import annotations

from datetime import date, datetime

from market_observer.domain.models import (
    DEFAULT_DISCLAIMER,
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


def test_symbol_is_uppercased() -> None:
    assert SymbolSnapshot(symbol="aapl", as_of=date(2026, 5, 31)).symbol == "AAPL"
    assert OptionsSignal(symbol=" msft ").symbol == "MSFT"
    assert SymbolNarrative(symbol="nvda", narrative="x").symbol == "NVDA"


def test_technicals_default_all_none() -> None:
    t = TechnicalIndicators()
    assert t.rsi_14 is None and t.macd is None and t.rel_volume is None


def test_snapshot_round_trip() -> None:
    snap = SymbolSnapshot(
        symbol="AAPL",
        as_of=date(2026, 5, 31),
        last_price=200.5,
        technicals=TechnicalIndicators(rsi_14=55.0, macd=1.2),
        options=OptionsSignal(symbol="AAPL", has_data=True, iv_skew=0.03),
    )
    dumped = snap.model_dump_json()
    again = SymbolSnapshot.model_validate_json(dumped)
    assert again == snap


def test_briefing_round_trip_and_defaults() -> None:
    data = BriefingData(
        as_of=date(2026, 5, 31),
        macro=MacroContext(
            as_of=date(2026, 5, 31),
            quotes=[MacroQuote(name="VIX", symbol="^VIX", value=14.2, pct_change_1d=-1.1)],
        ),
        symbols=[SymbolSnapshot(symbol="SPY", as_of=date(2026, 5, 31))],
    )
    briefing = Briefing(generated_at=datetime(2026, 5, 31, 8, 0, 0), data=data)
    assert briefing.disclaimer == DEFAULT_DISCLAIMER
    assert briefing.synthesis is None
    again = Briefing.model_validate_json(briefing.model_dump_json())
    assert again == briefing


def test_specialist_failed_helper() -> None:
    out = SpecialistOutput.failed("technical", "llm timeout")
    assert out.ok is False
    assert out.error == "llm timeout"
    assert out.per_symbol == {}


def test_synth_failed_helper() -> None:
    out = SynthesizerOutput.failed("boom")
    assert out.ok is False and out.error == "boom" and out.symbols == []
