"""Discord embeds renderer (render/discord.py)."""

from __future__ import annotations

from datetime import date, datetime

from market_observer.domain.models import (
    Briefing,
    BriefingData,
    MacroContext,
    MacroQuote,
    OptionsSignal,
    SymbolNarrative,
    SymbolSnapshot,
    SynthesizerOutput,
    TechnicalIndicators,
)
from market_observer.render.discord import (
    MAX_CHARS_PER_MESSAGE,
    MAX_EMBEDS_PER_MESSAGE,
    batch_embeds,
    build_embeds,
    embed_char_count,
    render_embed_messages,
)


def _briefing(n_symbols: int = 1, structured: bool = True) -> Briefing:
    symbols = []
    narratives = []
    for i in range(n_symbols):
        sym = f"SYM{i}"
        symbols.append(
            SymbolSnapshot(
                symbol=sym,
                as_of=date(2026, 6, 1),
                last_price=200.0 + i,
                technicals=TechnicalIndicators(rsi_14=55.0, macd_hist=0.5, price_vs_sma20_pct=1.2),
                options=OptionsSignal(symbol=sym, has_data=True, front_atm_iv=0.25, implied_move_pct=4.0),
            )
        )
        if structured:
            narratives.append(
                SymbolNarrative(symbol=sym, recent="涨了", why="有新闻", forecast="偏多（模型估计，非投资建议）")
            )
    data = BriefingData(
        as_of=date(2026, 6, 1),
        macro=MacroContext(
            as_of=date(2026, 6, 1),
            quotes=[MacroQuote(name="VIX", symbol="^VIX", value=14.2, pct_change_1d=-2.0)],
        ),
        symbols=symbols,
    )
    return Briefing(
        generated_at=datetime(2026, 6, 1, 8, 0),
        data=data,
        synthesis=SynthesizerOutput(ok=True, overall_summary="整体平稳。", symbols=narratives),
    )


def test_build_embeds_overview_plus_one_per_symbol() -> None:
    embeds = build_embeds(_briefing(n_symbols=3))
    assert len(embeds) == 1 + 3
    assert embeds[0]["title"].startswith("📊")
    # each symbol embed has a coloured stripe + fields
    for e in embeds[1:]:
        assert "color" in e
        assert e["fields"]


def test_symbol_embed_includes_structured_narrative() -> None:
    embeds = build_embeds(_briefing(n_symbols=1, structured=True))
    desc = embeds[1]["description"]
    assert "近况" in desc and "归因" in desc and "预测" in desc


def test_batch_respects_count_limit() -> None:
    embeds = build_embeds(_briefing(n_symbols=25))  # 26 embeds total
    batches = batch_embeds(embeds)
    assert all(len(b) <= MAX_EMBEDS_PER_MESSAGE for b in batches)
    assert sum(len(b) for b in batches) == len(embeds)


def test_batch_respects_char_limit() -> None:
    big = [{"title": "x" * 1000, "description": "y" * 4000, "fields": []} for _ in range(5)]
    batches = batch_embeds(big, max_per_message=10, max_chars=MAX_CHARS_PER_MESSAGE)
    assert all(sum(embed_char_count(e) for e in b) <= MAX_CHARS_PER_MESSAGE for b in batches)
    assert len(batches) > 1


def test_render_embed_messages_end_to_end() -> None:
    messages = render_embed_messages(_briefing(n_symbols=2))
    assert messages
    assert all(isinstance(m, list) for m in messages)


def test_embed_char_count_uses_utf8_bytes() -> None:
    # CJK chars are 3 bytes each in UTF-8; Discord counts bytes, not len().
    embed = {"title": "你好", "description": "", "fields": []}
    assert embed_char_count(embed) == len("你好".encode()) == 6


def test_batch_caps_dense_cjk_message_under_byte_budget() -> None:
    # Regression: dense CJK cards used to overflow Discord's real (byte) limit
    # and 500. Every message must stay within the embed-count and byte budget.
    embeds = build_embeds(_briefing(n_symbols=10))
    batches = batch_embeds(embeds)
    assert all(len(b) <= MAX_EMBEDS_PER_MESSAGE for b in batches)
    assert all(sum(embed_char_count(e) for e in b) <= MAX_CHARS_PER_MESSAGE for b in batches)
    assert sum(len(b) for b in batches) == len(embeds)
