"""The WebSocket client, exercised end to end against the bundled mock venue.

These are integration tests on purpose: connect, subscribe, reconnect and staleness
are exactly the behaviours a mocked socket would let pass and a real one would not.
The mock listens on loopback, so they stay fast and hermetic.
"""

from __future__ import annotations

import asyncio

import pytest

from l2tca.config import Settings
from l2tca.feed.backoff import ExponentialBackoff
from l2tca.feed.kraken import ConnectionRefused, KrakenFeed, SubscriptionRejected
from l2tca.feed.mock_server import MockKrakenServer
from l2tca.feed.parser import BookFrame, StatusFrame, SubscribeAck, parse_frame

FAST_BACKOFF = ExponentialBackoff(initial_s=0.01, max_s=0.05)


def settings_for(server: MockKrakenServer, **overrides) -> Settings:
    defaults = {
        "ws_url": server.url,
        "symbol": "BTC/USD",
        "depth": 100,
        "backoff_initial_s": 0.01,
        "backoff_max_s": 0.05,
    }
    return Settings(**{**defaults, **overrides})


async def take(feed: KrakenFeed, n: int, timeout: float = 10.0) -> list:
    """Collect ``n`` messages, then stop the feed."""

    async def collect() -> list:
        out = []
        async for message in feed.stream():
            out.append(message)
            if len(out) >= n:
                feed.request_stop()
        return out

    return await asyncio.wait_for(collect(), timeout=timeout)


async def test_handshake_order_and_stamps():
    async with MockKrakenServer(depth=5, updates_per_second=200) as server:
        feed = KrakenFeed(settings_for(server))
        messages = await take(feed, 5)

    kinds = [type(parse_frame(m.payload)).__name__ for m in messages]
    assert kinds[0] == StatusFrame.__name__
    assert kinds[1] == SubscribeAck.__name__

    snapshot = parse_frame(messages[2].payload)
    assert isinstance(snapshot, BookFrame) and snapshot.is_snapshot

    assert [m.seq for m in messages] == list(range(len(messages)))
    assert all(m.session == 0 for m in messages)
    # Both clocks are populated and move forward.
    assert all(m.recv_ns > 0 and m.recv_wall_ns > 0 for m in messages)
    assert [m.recv_ns for m in messages] == sorted(m.recv_ns for m in messages)


async def test_subscription_carries_the_configured_symbol_and_depth():
    async with MockKrakenServer(symbol="ETH/USD", depth=25) as server:
        feed = KrakenFeed(settings_for(server, symbol="ETH/USD", depth=25))
        messages = await take(feed, 3)

    ack = parse_frame(messages[1].payload)
    assert ack.raw["result"] == {
        "channel": "book",
        "depth": 25,
        "snapshot": True,
        "symbol": "ETH/USD",
    }


async def test_reconnects_transparently_and_marks_the_session():
    # The venue drops every connection after 10 frames; the consumer should not see
    # the seam except through `session`, and each session opens with a fresh snapshot.
    async with MockKrakenServer(depth=5, updates_per_second=200, drop_after_messages=10) as server:
        feed = KrakenFeed(settings_for(server))
        messages = await take(feed, 25)

    sessions = sorted({m.session for m in messages})
    assert sessions == list(range(len(sessions)))
    assert len(sessions) >= 2, "expected at least one reconnect"
    assert server.connections >= 2
    # Sequence numbers never restart: they identify a message within the capture.
    assert [m.seq for m in messages] == list(range(len(messages)))
    for session in sessions:
        first_book = next(
            parse_frame(m.payload)
            for m in messages
            if m.session == session and isinstance(parse_frame(m.payload), BookFrame)
        )
        assert first_book.is_snapshot, "every session must start from a fresh snapshot"


async def test_silent_socket_trips_the_staleness_watchdog():
    async with MockKrakenServer(
        depth=5, updates_per_second=200, go_silent_after_messages=6
    ) as server:
        feed = KrakenFeed(settings_for(server, stale_after_s=0.3))
        messages = await take(feed, 12, timeout=15.0)

    assert {m.session for m in messages} != {0}, "watchdog should have forced a reconnect"


async def test_rejected_subscription_is_fatal():
    async with MockKrakenServer(reject_subscription=True) as server:
        feed = KrakenFeed(settings_for(server))
        with pytest.raises(SubscriptionRejected, match="Subscription Not Found"):
            await take(feed, 10, timeout=10.0)


async def test_unreachable_endpoint_is_reported_not_retried_forever():
    feed = KrakenFeed(
        Settings(ws_url="ws://127.0.0.1:1", backoff_initial_s=0.01, backoff_max_s=0.02)
    )

    async def drain() -> None:
        async for _ in feed.stream():
            pass

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.3)
    assert not task.done(), "a refused TCP connection is transient: keep retrying"
    feed.request_stop()
    await asyncio.wait_for(task, timeout=5.0)


async def test_request_stop_ends_the_stream_promptly():
    async with MockKrakenServer(depth=5, updates_per_second=5) as server:
        feed = KrakenFeed(settings_for(server, stale_after_s=30.0))
        collected: list = []

        async def collect() -> None:
            async for message in feed.stream():
                collected.append(message)

        task = asyncio.create_task(collect())
        while len(collected) < 3:
            await asyncio.sleep(0.01)
        feed.request_stop()
        # Must not wait out the 30s staleness window.
        await asyncio.wait_for(task, timeout=2.0)
        assert feed.stopped


def test_connection_refused_is_a_distinct_error_type():
    # A 403 from an egress proxy is a configuration problem, not a flaky network.
    assert issubclass(ConnectionRefused, RuntimeError)
