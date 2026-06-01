"""Domain data models for the briefing pipeline.

Two layers:

* **Pre-LLM data** (pure facts computed by code): ``TechnicalIndicators``,
  ``OptionsSignal``, ``EventInfo``, ``SymbolSnapshot``, ``MacroQuote``,
  ``MacroContext``, ``BriefingData``.
* **Post-LLM** (agent interpretation): ``SpecialistOutput``,
  ``SymbolNarrative``, ``SynthesizerOutput``, and the final ``Briefing``.

This project is read-only analytics, not money movement, so ``float`` is used
throughout (unlike the main trading system, which mandates ``Decimal``).
Missing/insufficient data is represented as ``None`` — never fabricated.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


def _upper(v: str) -> str:
    return v.strip().upper()


# --------------------------------------------------------------------------
# Pre-LLM: pure facts
# --------------------------------------------------------------------------
class TechnicalIndicators(BaseModel):
    """Code-computed technical indicators for one symbol. All optional;
    ``None`` means insufficient history to compute."""

    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    price_vs_sma20_pct: float | None = None
    price_vs_sma50_pct: float | None = None
    price_vs_sma200_pct: float | None = None
    realized_vol_20: float | None = None  # annualized, percent
    atr_14: float | None = None
    range_position_pct: float | None = None  # position in N-day range, 0..100
    rel_volume: float | None = None  # today volume / 20d avg volume


class OptionsSignal(BaseModel):
    """Code-computed EOD options signals for one symbol."""

    symbol: str
    has_data: bool = False
    front_atm_iv: float | None = None  # nearest expiry ATM implied vol
    next_atm_iv: float | None = None  # next expiry ATM implied vol
    term_structure: float | None = None  # next_atm_iv - front_atm_iv
    term_structure_inverted: bool | None = None  # front > next (event pricing)
    put_call_volume_ratio: float | None = None
    put_call_oi_ratio: float | None = None
    iv_skew: float | None = None  # OTM put IV - OTM call IV
    note: str | None = None  # e.g. "insufficient option chain"

    _norm_symbol = field_validator("symbol")(_upper)


class EventInfo(BaseModel):
    """Upcoming corporate events near the as-of date."""

    next_earnings_date: date | None = None
    days_to_earnings: int | None = None
    next_ex_dividend_date: date | None = None


class SymbolSnapshot(BaseModel):
    """All code-computed facts for one symbol on one day."""

    symbol: str
    as_of: date
    last_price: float | None = None
    technicals: TechnicalIndicators = Field(default_factory=TechnicalIndicators)
    options: OptionsSignal | None = None
    event: EventInfo | None = None

    _norm_symbol = field_validator("symbol")(_upper)


class MacroQuote(BaseModel):
    """One macro instrument reading."""

    name: str  # human label, e.g. "VIX"
    symbol: str  # source ticker, e.g. "^VIX"
    value: float | None = None
    pct_change_1d: float | None = None


class MacroContext(BaseModel):
    """The day's macro backdrop."""

    as_of: date
    quotes: list[MacroQuote] = Field(default_factory=list)


class BriefingData(BaseModel):
    """Pure-data layer assembled before any LLM call."""

    as_of: date
    macro: MacroContext
    symbols: list[SymbolSnapshot] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Post-LLM: agent interpretation
# --------------------------------------------------------------------------
class SpecialistOutput(BaseModel):
    """Output of a specialist analyst agent (technical / options / macro)."""

    agent_name: str
    ok: bool
    overall: str = ""  # domain-level takeaway across the watchlist
    per_symbol: dict[str, str] = Field(default_factory=dict)  # symbol -> note
    error: str | None = None

    @classmethod
    def failed(cls, agent_name: str, error: str) -> SpecialistOutput:
        return cls(agent_name=agent_name, ok=False, error=error)


class SymbolNarrative(BaseModel):
    symbol: str
    narrative: str

    _norm_symbol = field_validator("symbol")(_upper)


class SynthesizerOutput(BaseModel):
    """Output of the editor/synthesizer agent."""

    ok: bool
    overall_summary: str = ""
    symbols: list[SymbolNarrative] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def failed(cls, error: str) -> SynthesizerOutput:
        return cls(ok=False, error=error)


DEFAULT_DISCLAIMER = (
    "本简报由 market-observer 自动生成，仅供研究参考，非投资建议。"
    "数据来自免费源，可能延迟或不完整。请勿据此直接交易。"
)


class Briefing(BaseModel):
    """The complete briefing: pure data + all agent outputs."""

    generated_at: datetime
    data: BriefingData
    technical: SpecialistOutput | None = None
    options: SpecialistOutput | None = None
    macro_analysis: SpecialistOutput | None = None
    synthesis: SynthesizerOutput | None = None
    disclaimer: str = DEFAULT_DISCLAIMER
