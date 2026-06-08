"""Discord webhook push, with chunking under Discord's 2000-char limit.

``chunk_text`` is a pure function (unit-testable). ``DiscordNotifier`` posts
each chunk via an injectable httpx.Client.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DISCORD_HARD_LIMIT = 2000
SAFE_LIMIT = 1900  # headroom for Discord's own formatting/counting


class DiscordError(Exception):
    """Raised when a webhook post fails."""


def chunk_text(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Split text into <= limit chunks on line boundaries; hard-split any
    single line longer than limit."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    for line in text.split("\n"):
        if len(line) > limit:
            flush()
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue
        added = len(line) + (1 if current else 0)
        if current_len + added > limit:
            flush()
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added
    flush()
    return chunks


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        http_client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self.webhook_url = webhook_url
        self._client = http_client or httpx.Client(timeout=timeout)

    def send(self, text: str) -> int:
        """Post text (chunked). Returns the number of messages sent."""
        chunks = chunk_text(text)
        for idx, chunk in enumerate(chunks):
            try:
                resp = self._client.post(self.webhook_url, json={"content": chunk})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise DiscordError(
                    f"discord post failed on chunk {idx + 1}/{len(chunks)}: {exc}"
                ) from exc
        logger.info("sent %d discord message(s)", len(chunks))
        return len(chunks)

    def send_embeds(self, batches: list[list[dict[str, Any]]]) -> int:
        """Post coloured embed cards. ``batches`` is a list of per-message
        ``embeds`` arrays (already split to respect Discord's <=10 embeds and
        <=6000 chars per message). Returns the number of messages sent."""
        messages = [b for b in batches if b]
        for idx, embeds in enumerate(messages):
            try:
                resp = self._client.post(self.webhook_url, json={"embeds": embeds})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise DiscordError(
                    f"discord embed post failed on message {idx + 1}/{len(messages)}: {exc}"
                ) from exc
        logger.info("sent %d discord embed message(s)", len(messages))
        return len(messages)
