"""Thin entry point: wire real implementations and run the daily briefing.

This is the only module that constructs network/LLM clients. Everything it
calls is pure/injectable and covered by tests with fakes. Run with::

    uv run python -m market_observer.run_briefing

Behaviour degrades gracefully:
  * no DeepSeek key  -> data-only briefing (no narrative)
  * no Discord webhook -> briefing is written to disk only
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from market_observer.agents.llm_client import DeepSeekClient, LLMClient
from market_observer.config import Settings, load_settings
from market_observer.data.yfinance_provider import YFinanceProvider
from market_observer.notify.discord import DiscordError, DiscordNotifier
from market_observer.pipeline import generate_briefing
from market_observer.render.markdown import render_briefing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("market_observer.run")


def _build_llm(settings: Settings) -> LLMClient | None:
    if not settings.deepseek_api_key:
        logger.warning("MO_DEEPSEEK_API_KEY not set; running data-only briefing")
        return None
    return DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _save(markdown: str, output_dir: str, as_of: date) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"briefing_{as_of.isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info("briefing written to %s", path)
    return path


def _push(settings: Settings, markdown: str) -> None:
    if not settings.discord_webhook_url:
        logger.warning("MO_DISCORD_WEBHOOK_URL not set; skipping Discord push")
        return
    notifier = DiscordNotifier(webhook_url=settings.discord_webhook_url)
    try:
        sent = notifier.send(markdown)
        logger.info("pushed briefing to Discord in %d message(s)", sent)
    except DiscordError:
        logger.exception("Discord push failed; briefing is still saved on disk")


def main() -> None:
    settings = load_settings()
    as_of = date.today()
    now = datetime.now()

    provider = YFinanceProvider()
    llm = _build_llm(settings)

    logger.info("generating briefing for %s", as_of.isoformat())
    briefing = generate_briefing(
        provider,
        llm,
        pinned=settings.pinned_symbol_list,
        size=settings.watchlist_size,
        as_of=as_of,
        now=now,
    )
    markdown = render_briefing(briefing)
    _save(markdown, settings.output_dir, as_of)
    _push(settings, markdown)
    logger.info("done")


if __name__ == "__main__":
    main()
