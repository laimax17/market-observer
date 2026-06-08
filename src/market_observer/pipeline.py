"""End-to-end assembly: provider -> BriefingData -> Briefing.

Kept free of network/LLM construction so it is fully testable with fakes.
``run_briefing.py`` wires the real implementations into these functions.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime

from market_observer.agents.llm_client import LLMClient
from market_observer.agents.orchestrator import run_briefing as run_agent_dag
from market_observer.data.events import build_event_info
from market_observer.data.macro import build_macro_context
from market_observer.data.market_data import build_symbol_snapshot
from market_observer.data.news import build_recent_news
from market_observer.data.options_data import build_options_signal
from market_observer.data.provider import MarketDataProvider
from market_observer.data.watchlist import build_watchlist
from market_observer.domain.models import Briefing, BriefingData

logger = logging.getLogger(__name__)


def assemble_briefing_data(
    provider: MarketDataProvider,
    symbols: Sequence[str],
    as_of: date,
) -> BriefingData:
    """Build the pure-data layer: per-symbol snapshot (technicals + options +
    event) plus the macro backdrop."""
    snapshots = []
    for sym in symbols:
        snap = build_symbol_snapshot(provider, sym, as_of)
        options = build_options_signal(provider, sym, snap.last_price, as_of=as_of)
        event = build_event_info(provider, sym, as_of)
        news = build_recent_news(provider, sym)
        snapshots.append(
            snap.model_copy(update={"options": options, "event": event, "news": news})
        )
    macro = build_macro_context(provider, as_of)
    return BriefingData(as_of=as_of, macro=macro, symbols=snapshots)


def generate_briefing(
    provider: MarketDataProvider,
    llm: LLMClient | None,
    pinned: Sequence[str],
    size: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> Briefing:
    """Full pipeline. If ``llm`` is None, returns a data-only briefing (no
    narrative) so the system still works when LLM credentials are absent."""
    as_of = as_of or date.today()
    now = now or datetime.now()

    watchlist = build_watchlist(provider, pinned, size)
    logger.info("watchlist: %s", watchlist)
    data = assemble_briefing_data(provider, watchlist, as_of)

    if llm is None:
        logger.warning("no LLM configured; producing data-only briefing")
        return Briefing(generated_at=now, data=data)

    return run_agent_dag(llm, data, now=now)
