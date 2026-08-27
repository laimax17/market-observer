"""Decode Kraken WebSocket v2 frames into typed events.

This is deliberately *only* decoding: it turns one JSON text frame into a small
immutable object and does no book maintenance, no sequencing and no validation
beyond what is needed to classify the frame. Book state lives in
:mod:`l2tca.book`.

Reference shapes (Kraken v2, ``book`` channel)::

    {"channel": "status", "type": "update", "data": [{"system": "online", ...}]}
    {"method": "subscribe", "req_id": 1, "success": true, "result": {...}}
    {"channel": "heartbeat"}
    {"channel": "book", "type": "snapshot",
     "data": [{"symbol": "BTC/USD",
               "bids": [{"price": 64000.1, "qty": 1.5}, ...],
               "asks": [{"price": 64000.2, "qty": 0.8}, ...],
               "checksum": 2438155326}]}
    {"channel": "book", "type": "update",
     "data": [{"symbol": "BTC/USD", "bids": [{"price": 64000.1, "qty": 0.0}],
               "asks": [], "checksum": 1234567890,
               "timestamp": "2026-08-27T12:00:00.123456Z"}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

BookSide = Literal["bid", "ask"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Level:
    """One price level as published by the venue.

    ``qty == 0`` in an *update* is a deletion, not a zero-size resting order.
    """

    price: float
    qty: float


@dataclass(frozen=True, slots=True)
class Frame:
    """Base class for every decoded frame. ``raw`` keeps the original mapping."""

    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class StatusFrame(Frame):
    system: str
    api_version: str
    connection_id: int | None


@dataclass(frozen=True, slots=True)
class SubscribeAck(Frame):
    success: bool
    req_id: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class HeartbeatFrame(Frame):
    pass


@dataclass(frozen=True, slots=True)
class PongFrame(Frame):
    req_id: int | None


@dataclass(frozen=True, slots=True)
class BookFrame(Frame):
    """A ``book`` channel snapshot or delta for one symbol.

    Kraken batches one entry per symbol inside ``data``; a single-symbol
    subscription therefore yields exactly one :class:`BookFrame` per wire frame.
    """

    symbol: str
    is_snapshot: bool
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    checksum: int | None
    exchange_ts_ns: int | None


@dataclass(frozen=True, slots=True)
class ErrorFrame(Frame):
    error: str
    req_id: int | None


@dataclass(frozen=True, slots=True)
class UnknownFrame(Frame):
    """Anything this decoder does not model — kept rather than dropped."""


class FrameDecodeError(ValueError):
    """The frame was not valid JSON, or a modelled field had the wrong shape."""


def parse_timestamp_ns(value: str | None) -> int | None:
    """Parse Kraken's RFC 3339 timestamp into nanoseconds since the epoch.

    Tolerates a trailing ``Z`` and any number of fractional digits, both of which
    ``datetime.fromisoformat`` is picky about before Python 3.12. Returns ``None``
    for a missing or unparsable value: a bad exchange timestamp must never take the
    feed down, because ``recv_wall_ns`` already gives a usable time axis.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                rest = tail[i:]
                break
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # Integer arithmetic, not int(dt.timestamp() * 1e9): a float64 holds ~53 bits of
    # mantissa, and nanoseconds since 1970 need ~61, so the naive version silently
    # rounds to the nearest ~256ns. That is larger than several of the intervals this
    # project measures.
    delta = dt - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _levels(entries: Any) -> tuple[Level, ...]:
    if not entries:
        return ()
    if not isinstance(entries, list):
        raise FrameDecodeError(f"expected a list of levels, got {type(entries).__name__}")
    out = []
    for entry in entries:
        try:
            out.append(Level(price=float(entry["price"]), qty=float(entry["qty"])))
        except (TypeError, KeyError, ValueError) as exc:
            raise FrameDecodeError(f"malformed price level: {entry!r}") from exc
    return tuple(out)


def parse_frame(payload: str) -> Frame:
    """Decode one WebSocket text frame.

    Raises :class:`FrameDecodeError` on invalid JSON or a malformed *modelled*
    frame. Frames this decoder does not model come back as :class:`UnknownFrame`,
    so a venue adding a field or a channel never crashes a recording run.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FrameDecodeError(f"not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise FrameDecodeError(f"expected a JSON object, got {type(obj).__name__}")

    channel = obj.get("channel")
    method = obj.get("method")

    if channel == "heartbeat":
        return HeartbeatFrame(raw=obj)

    if channel == "book":
        data = obj.get("data") or []
        if not isinstance(data, list) or not data:
            raise FrameDecodeError("book frame carried no data entries")
        entry = data[0]
        if not isinstance(entry, dict):
            raise FrameDecodeError(f"book data entry was {type(entry).__name__}, not an object")
        checksum = entry.get("checksum")
        return BookFrame(
            raw=obj,
            symbol=str(entry.get("symbol", "")),
            is_snapshot=obj.get("type") == "snapshot",
            bids=_levels(entry.get("bids")),
            asks=_levels(entry.get("asks")),
            checksum=int(checksum) if checksum is not None else None,
            exchange_ts_ns=parse_timestamp_ns(entry.get("timestamp")),
        )

    if channel == "status":
        data = obj.get("data") or [{}]
        entry = data[0] if isinstance(data, list) and data else {}
        connection_id = entry.get("connection_id")
        return StatusFrame(
            raw=obj,
            system=str(entry.get("system", "unknown")),
            api_version=str(entry.get("api_version", "")),
            connection_id=int(connection_id) if connection_id is not None else None,
        )

    if method in {"subscribe", "unsubscribe"}:
        return SubscribeAck(
            raw=obj,
            success=bool(obj.get("success", False)),
            req_id=obj.get("req_id"),
            error=obj.get("error"),
        )

    if method == "pong":
        return PongFrame(raw=obj, req_id=obj.get("req_id"))

    if "error" in obj:
        return ErrorFrame(raw=obj, error=str(obj["error"]), req_id=obj.get("req_id"))

    return UnknownFrame(raw=obj)
