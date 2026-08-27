"""A local stand-in for Kraken's public WebSocket v2 ``book`` channel.

It exists for three reasons:

1. CI and unit tests can exercise the *real* :class:`~l2tca.feed.kraken.KrakenFeed`
   end to end — connect, subscribe, ack, snapshot, deltas, heartbeat, disconnect,
   reconnect — with no external network and no flakiness.
2. Development works offline, on a plane or behind an egress policy that blocks the
   venue.
3. It generates the committed sample recording under ``data/samples/``.

Frame shapes match Kraken v2. **Checksums do not**: the server emits a random
uint32 in the ``checksum`` field, because computing the real CRC32 is part of the
order book work this project deliberately leaves unimplemented. Validate a checksum
implementation against a live capture, never against mock data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import websockets
import websockets.exceptions

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(slots=True)
class MockBookState:
    """A deterministic random walk that produces plausible top-of-book behaviour."""

    symbol: str = "BTC/USD"
    depth: int = 100
    mid: float = 64_000.0
    tick: float = 0.1
    seed: int = 7
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def snapshot(self) -> dict[str, Any]:
        bids = [
            {
                "price": round(self.mid - self.tick * (i + 1), 5),
                "qty": round(self.rng.uniform(0.01, 5.0), 8),
            }
            for i in range(self.depth)
        ]
        asks = [
            {
                "price": round(self.mid + self.tick * (i + 1), 5),
                "qty": round(self.rng.uniform(0.01, 5.0), 8),
            }
            for i in range(self.depth)
        ]
        return {
            "symbol": self.symbol,
            "bids": bids,
            "asks": asks,
            "checksum": self.rng.getrandbits(32),
        }

    def update(self) -> dict[str, Any]:
        """One delta: a few levels changed, sometimes a deletion (``qty == 0``)."""
        self.mid += self.rng.choice((-1, 0, 1)) * self.tick
        bids: list[dict[str, float]] = []
        asks: list[dict[str, float]] = []
        for _ in range(self.rng.randint(1, 3)):
            side = bids if self.rng.random() < 0.5 else asks
            offset = self.tick * self.rng.randint(1, self.depth)
            price = self.mid - offset if side is bids else self.mid + offset
            qty = 0.0 if self.rng.random() < 0.15 else round(self.rng.uniform(0.01, 5.0), 8)
            side.append({"price": round(price, 5), "qty": qty})
        return {
            "symbol": self.symbol,
            "bids": bids,
            "asks": asks,
            "checksum": self.rng.getrandbits(32),
            "timestamp": _now_iso(),
        }


class MockKrakenServer:
    """Serve the mock feed on an ephemeral localhost port.

    Parameters
    ----------
    updates_per_second:
        Delta rate. The real BTC/USD book runs at roughly 10-50 messages/second.
    heartbeat_interval_s:
        Kraken sends ``{"channel": "heartbeat"}`` about once a second.
    drop_after_messages:
        Close the connection abruptly after this many frames, to exercise reconnect
        and backoff. ``None`` (default) never drops.
    go_silent_after_messages:
        Stop sending anything (including heartbeats) while holding the socket open,
        to exercise the staleness watchdog.
    """

    def __init__(
        self,
        *,
        symbol: str = "BTC/USD",
        depth: int = 100,
        updates_per_second: float = 25.0,
        heartbeat_interval_s: float = 1.0,
        seed: int = 7,
        drop_after_messages: int | None = None,
        go_silent_after_messages: int | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.symbol = symbol
        self.depth = depth
        self.updates_per_second = updates_per_second
        self.heartbeat_interval_s = heartbeat_interval_s
        self.seed = seed
        self.drop_after_messages = drop_after_messages
        self.go_silent_after_messages = go_silent_after_messages
        self.host = host
        self.port = port
        self._server: Any | None = None
        self.connections = 0

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("server is not running")
        sock = next(iter(self._server.sockets))
        host, port = sock.getsockname()[:2]
        return f"ws://{host}:{port}"

    async def start(self) -> MockKrakenServer:
        self._server = await websockets.serve(self._handle, self.host, self.port)
        log.info("mock Kraken server listening on %s", self.url)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> MockKrakenServer:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def _handle(self, websocket: Any) -> None:
        self.connections += 1
        # Each connection walks the same seeded path, so a replayed test is
        # reproducible no matter how many reconnects it went through.
        state = MockBookState(symbol=self.symbol, depth=self.depth, seed=self.seed)
        sent = 0
        try:
            await websocket.send(
                json.dumps(
                    {
                        "channel": "status",
                        "type": "update",
                        "data": [
                            {
                                "api_version": "v2",
                                "connection_id": state.rng.getrandbits(48),
                                "system": "online",
                                "version": "2.0.11",
                            }
                        ],
                    }
                )
            )
            sent += 1

            request = json.loads(await websocket.recv())
            params = request.get("params", {})
            await websocket.send(
                json.dumps(
                    {
                        "method": "subscribe",
                        "req_id": request.get("req_id"),
                        "result": {
                            "channel": "book",
                            "depth": params.get("depth", self.depth),
                            "snapshot": True,
                            "symbol": (params.get("symbol") or [self.symbol])[0],
                        },
                        "success": True,
                        "time_in": _now_iso(),
                        "time_out": _now_iso(),
                    }
                )
            )
            sent += 1

            await websocket.send(
                json.dumps({"channel": "book", "type": "snapshot", "data": [state.snapshot()]})
            )
            sent += 1

            interval = 1.0 / self.updates_per_second
            next_heartbeat = self.heartbeat_interval_s
            elapsed = 0.0
            while True:
                if self.drop_after_messages is not None and sent >= self.drop_after_messages:
                    await websocket.close(code=1011, reason="mock drop")
                    return
                if (
                    self.go_silent_after_messages is not None
                    and sent >= self.go_silent_after_messages
                ):
                    await asyncio.Future()  # hold the socket open, send nothing
                await asyncio.sleep(interval)
                elapsed += interval
                if elapsed >= next_heartbeat:
                    await websocket.send(json.dumps({"channel": "heartbeat"}))
                    sent += 1
                    next_heartbeat += self.heartbeat_interval_s
                await websocket.send(
                    json.dumps({"channel": "book", "type": "update", "data": [state.update()]})
                )
                sent += 1
        except websockets.exceptions.ConnectionClosed:
            return
        except asyncio.CancelledError:
            raise


async def serve_forever(**kwargs: Any) -> None:
    """Run the mock server until cancelled — used by ``l2tca mock-server``."""
    async with MockKrakenServer(**kwargs) as server:
        print(f"mock Kraken v2 server listening on {server.url}")
        print("point the recorder at it:")
        print(f"  uv run l2tca record --url {server.url} --duration 60")
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Future()
