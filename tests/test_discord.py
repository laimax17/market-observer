"""T-10: Discord chunking + notifier over MockTransport."""

from __future__ import annotations

import httpx
import pytest

from market_observer.notify.discord import (
    SAFE_LIMIT,
    DiscordError,
    DiscordNotifier,
    chunk_text,
)


def test_chunk_short_text_single_chunk() -> None:
    assert chunk_text("hello\nworld") == ["hello\nworld"]


def test_chunk_respects_limit() -> None:
    text = "\n".join("x" * 50 for _ in range(100))  # ~5100 chars
    chunks = chunk_text(text, limit=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # round-trips (modulo the join newlines we split on)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_chunk_hard_splits_overlong_line() -> None:
    line = "y" * 500
    chunks = chunk_text(line, limit=200)
    assert len(chunks) == 3
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == line


def test_default_limit_under_discord_hard_cap() -> None:
    assert SAFE_LIMIT < 2000


def _notifier(handler, **kw) -> DiscordNotifier:
    transport = httpx.MockTransport(handler)
    return DiscordNotifier(
        webhook_url="https://discord.test/webhook",
        http_client=httpx.Client(transport=transport),
        **kw,
    )


def test_send_posts_each_chunk() -> None:
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        posted.append(json.loads(request.content)["content"])
        return httpx.Response(204)

    text = "\n".join("z" * 100 for _ in range(60))  # ~6000 chars -> multiple chunks
    n = _notifier(handler).send(text)
    assert n == len(posted)
    assert n >= 2
    assert all(len(p) <= SAFE_LIMIT for p in posted)


def test_send_requires_webhook() -> None:
    with pytest.raises(ValueError):
        DiscordNotifier(webhook_url="")


def test_send_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(DiscordError):
        _notifier(handler).send("hello")
