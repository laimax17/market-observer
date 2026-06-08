"""Shared formatting + signal helpers (render/format.py)."""

from __future__ import annotations

from datetime import date, datetime

from market_observer.domain.models import (
    Briefing,
    BriefingData,
    MacroContext,
    MacroQuote,
    OptionsSignal,
    RecentAction,
    SymbolSnapshot,
    TechnicalIndicators,
)
from market_observer.render.format import (
    DASH,
    iv_pct,
    num,
    pct,
    price,
    signal_style,
    term_label,
    today_highlights,
)


def test_iv_pct_decimal_to_percent() -> None:
    assert iv_pct(0.272) == "27.2%"
    assert iv_pct(None) == DASH


def test_num_price_pct_formatting() -> None:
    assert num(None) == DASH
    assert price(1234.5) == "1,234.50"
    assert pct(1.23, signed=True) == "+1.2%"
    assert pct(-1.23) == "-1.2%"


def test_term_label_variants() -> None:
    deep = OptionsSignal(symbol="X", has_data=True, term_structure=-0.06, term_structure_inverted=True)
    shallow = OptionsSignal(symbol="X", has_data=True, term_structure=-0.01, term_structure_inverted=True)
    normal = OptionsSignal(symbol="X", has_data=True, term_structure=0.02, term_structure_inverted=False)
    assert term_label(deep) == "倒挂(深)"
    assert term_label(shallow) == "倒挂"
    assert term_label(normal) == "正常"
    assert term_label(None) == DASH


def _snap(**tech) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol="AAA",
        as_of=date(2026, 6, 1),
        last_price=100.0,
        technicals=TechnicalIndicators(**tech),
    )


def test_signal_style_strong_weak_neutral() -> None:
    strong = _snap(
        price_vs_sma20_pct=2.0, price_vs_sma50_pct=2.0, price_vs_sma200_pct=2.0, rsi_14=60, macd_hist=0.5
    )
    weak = _snap(
        price_vs_sma20_pct=-2.0, price_vs_sma50_pct=-2.0, price_vs_sma200_pct=-2.0, rsi_14=40, macd_hist=-0.5
    )
    neutral = _snap(price_vs_sma20_pct=1.0, price_vs_sma200_pct=-1.0)
    assert signal_style(strong).label == "强势"
    assert signal_style(weak).label == "弱势"
    assert signal_style(neutral).label == "中性"


def test_signal_style_high_vol_suffix() -> None:
    s = _snap(price_vs_sma20_pct=2.0, price_vs_sma50_pct=2.0, price_vs_sma200_pct=2.0, realized_vol_20=75.0)
    assert "⚠️高波动" in signal_style(s).label


def _briefing(symbols: list[SymbolSnapshot], macro_quotes: list[MacroQuote]) -> Briefing:
    data = BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(as_of=date(2026, 6, 1), quotes=macro_quotes),
        symbols=symbols,
    )
    return Briefing(generated_at=datetime(2026, 6, 1, 8, 0), data=data)


def test_today_highlights_picks_macro_shock_and_rsi() -> None:
    snap = _snap(rsi_14=22.0)
    snap = snap.model_copy(update={"recent": RecentAction(ret_5d_pct=-12.0)})
    b = _briefing(
        [snap],
        [MacroQuote(name="VIX", symbol="^VIX", value=30.0, pct_change_1d=15.0)],
    )
    hl = today_highlights(b)
    assert any("VIX" in h for h in hl)
    assert any("超卖" in h for h in hl)
    assert any("近5日" in h for h in hl)


def test_today_highlights_empty_when_quiet() -> None:
    snap = _snap(rsi_14=50.0)
    b = _briefing([snap], [MacroQuote(name="VIX", symbol="^VIX", value=14.0, pct_change_1d=0.5)])
    assert today_highlights(b) == []
