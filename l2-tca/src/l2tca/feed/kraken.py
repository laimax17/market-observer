"""Kraken v2 WebSocket client: connect, subscribe, watch, reconnect, shut down.

The client hands the consumer :class:`~l2tca.feed.types.RawMessage` objects and does
nothing else with them: no book state, no parsing in the steady-state hot path. That
separation is what makes the recorder byte-for-byte faithful and keeps the
measurable cost of "receiving a message" down to a socket read plus two clock reads.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import websockets
import websockets.exceptions

from l2tca.config import Settings
from l2tca.feed.backoff import ExponentialBackoff
from l2tca.feed.types import RawMessage

log = logging.getLogger(__name__)

#: Errors that mean "this connection is gone, try another one".
TRANSIENT_ERRORS = (
    OSError,
    TimeoutError,
    websockets.exceptions.WebSocketException,
)


class SubscriptionRejected(RuntimeError):
    """The venue refused the subscription (bad symbol, bad depth, ...).

    Fatal on purpose: retrying a request the venue has already rejected just
    hammers the endpoint until it rate-limits the client.
    """


class StaleFeed(TimeoutError):
    """No frame arrived within the staleness window.

    Kraken emits a heartbeat every second on an idle subscription, so silence means
    the socket is dead in a way TCP has not noticed yet. Treated as transient.
    """


class KrakenFeed:
    """An auto-reconnecting subscription to one Kraken ``book`` channel.

    Usage::

        feed = KrakenFeed(Settings())
        async for msg in feed.stream():
            ...

    Reconnects are transparent to the consumer: :meth:`stream` keeps yielding across
    them, with :attr:`RawMessage.session` incrementing so downstream code can tell
    that book state must be rebuilt from the fresh snapshot.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connect: Callable[..., Any] | None = None,
        backoff: ExponentialBackoff | None = None,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.settings = settings or Settings()
        self._connect = connect or websockets.connect
        self._backoff = backoff or ExponentialBackoff(
            initial_s=self.settings.backoff_initial_s,
            max_s=self.settings.backoff_max_s,
        )
        self._monotonic_ns = monotonic_ns
        self._wall_ns = wall_ns

        self._stop = asyncio.Event()
        self._ws: Any | None = None
        self._seq = 0
        self._session = -1
        self._req_id = 0

    # ------------------------------------------------------------------ state

    @property
    def seq(self) -> int:
        """Number of messages yielded so far (also the next message's sequence)."""
        return self._seq

    @property
    def session(self) -> int:
        """Current connection generation; -1 before the first connection."""
        return self._session

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        """Ask the stream to finish.

        Safe to call from a signal handler. Sets the stop flag and closes the live
        socket so a blocked ``recv`` returns immediately instead of waiting out the
        staleness window.
        """
        self._stop.set()
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(ws.close())

    # ----------------------------------------------------------------- stream

    async def stream(self) -> AsyncIterator[RawMessage]:
        """Yield stamped raw frames until :meth:`request_stop` or a fatal error."""
        while not self._stop.is_set():
            connected_ns = self._monotonic_ns()
            try:
                async for message in self._run_once():
                    yield message
            except SubscriptionRejected:
                raise
            except TRANSIENT_ERRORS as exc:
                if self._stop.is_set():
                    break
                uptime_s = (self._monotonic_ns() - connected_ns) / 1e9
                if uptime_s >= self.settings.backoff_reset_after_s:
                    # The connection was healthy; this is a fresh failure, not a
                    # continuing outage, so start the ladder from the bottom again.
                    self._backoff.reset()
                delay = self._backoff.next_delay()
                log.warning(
                    "feed disconnected after %.1fs (%s: %s); reconnecting in %.2fs",
                    uptime_s,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                if await self._sleep_or_stop(delay):
                    break
            else:
                if self._stop.is_set():
                    break
                delay = self._backoff.next_delay()
                log.warning("feed closed cleanly; reconnecting in %.2fs", delay)
                if await self._sleep_or_stop(delay):
                    break

    async def _run_once(self) -> AsyncIterator[RawMessage]:
        """One connection: subscribe, then pump frames until it fails or stops."""
        async with self._connect(
            self.settings.ws_url,
            ping_interval=self.settings.ping_interval_s,
            ping_timeout=self.settings.ping_timeout_s,
            # permessage-deflate costs CPU on every frame and adds jitter to the
            # very latency this project measures. Book frames are small; skip it.
            compression=None,
            max_queue=1024,
        ) as ws:
            self._ws = ws
            self._session += 1
            log.info(
                "connected to %s (session %d), subscribing to %s depth=%d",
                self.settings.ws_url,
                self._session,
                self.settings.symbol,
                self.settings.depth,
            )
            try:
                await ws.send(self._subscribe_payload())
                ack_seen = False
                while not self._stop.is_set():
                    payload = await self._recv(ws)
                    if payload is None:
                        return
                    message = self._stamp(payload)
                    yield message
                    if not ack_seen:
                        # Parse only until the venue confirms the subscription; after
                        # that the read path stays parse-free.
                        ack_seen = self._check_subscription(message.payload)
            finally:
                self._ws = None

    async def _recv(self, ws: Any) -> str | None:
        """Read one text frame, or ``None`` once a stop has been requested.

        Raises :class:`StaleFeed` if nothing arrives within the staleness window.
        """
        try:
            async with asyncio.timeout(self.settings.stale_after_s):
                payload = await ws.recv()
        except TimeoutError as exc:
            raise StaleFeed(
                f"no frame for {self.settings.stale_after_s:.1f}s (heartbeat expected every 1s)"
            ) from exc
        except websockets.exceptions.ConnectionClosed:
            if self._stop.is_set():
                return None
            raise
        if isinstance(payload, bytes):  # Kraken speaks text; be defensive anyway.
            payload = payload.decode("utf-8", errors="replace")
        return payload

    def _stamp(self, payload: str) -> RawMessage:
        """Attach both clocks and the sequence. Kept minimal — it is on the hot path."""
        message = RawMessage(
            seq=self._seq,
            session=self._session,
            recv_ns=self._monotonic_ns(),
            recv_wall_ns=self._wall_ns(),
            payload=payload,
        )
        self._seq += 1
        return message

    def _subscribe_payload(self) -> str:
        self._req_id += 1
        return json.dumps(
            {
                "method": "subscribe",
                "params": {
                    "channel": "book",
                    "symbol": [self.settings.symbol],
                    "depth": self.settings.depth,
                    "snapshot": True,
                },
                "req_id": self._req_id,
            },
            separators=(",", ":"),
        )

    def _check_subscription(self, payload: str) -> bool:
        """Return True once a subscribe ack has been seen; raise if it was a refusal."""
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return False
        if not isinstance(obj, dict) or obj.get("method") != "subscribe":
            return False
        if obj.get("success"):
            log.info("subscription confirmed: %s", obj.get("result"))
            return True
        raise SubscriptionRejected(f"Kraken refused the subscription: {obj.get('error', obj)!r}")

    async def _sleep_or_stop(self, delay: float) -> bool:
        """Sleep, returning True if a stop was requested during the wait."""
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(delay):
                await self._stop.wait()
        return self._stop.is_set()
