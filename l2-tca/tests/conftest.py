"""Shared fixtures: canned Kraken frames and a recording built from them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from l2tca.feed.recorder import JsonlRecorder
from l2tca.feed.types import RawMessage

#: A fixed wall clock so partition and rotation assertions are deterministic.
BASE_WALL_NS = int(datetime(2026, 8, 27, 14, 30, 0, tzinfo=UTC).timestamp() * 1_000_000_000)


def status_payload() -> str:
    return json.dumps(
        {
            "channel": "status",
            "type": "update",
            "data": [
                {
                    "api_version": "v2",
                    "connection_id": 12345678901234,
                    "system": "online",
                    "version": "2.0.11",
                }
            ],
        }
    )


def ack_payload(req_id: int = 1, success: bool = True) -> str:
    body: dict = {"method": "subscribe", "req_id": req_id, "success": success}
    if success:
        body["result"] = {
            "channel": "book",
            "depth": 100,
            "snapshot": True,
            "symbol": "BTC/USD",
        }
    else:
        body["error"] = "Subscription Not Found"
    return json.dumps(body)


def snapshot_payload(levels: int = 3, mid: float = 64_000.0) -> str:
    return json.dumps(
        {
            "channel": "book",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bids": [{"price": mid - 0.1 * (i + 1), "qty": 1.0 + i} for i in range(levels)],
                    "asks": [{"price": mid + 0.1 * (i + 1), "qty": 2.0 + i} for i in range(levels)],
                    "checksum": 1234567890,
                }
            ],
        }
    )


def update_payload(price: float, qty: float, side: str = "bid") -> str:
    entry = {
        "symbol": "BTC/USD",
        "bids": [{"price": price, "qty": qty}] if side == "bid" else [],
        "asks": [] if side == "bid" else [{"price": price, "qty": qty}],
        "checksum": 987654321,
        "timestamp": "2026-08-27T14:30:01.123456Z",
    }
    return json.dumps({"channel": "book", "type": "update", "data": [entry]})


def heartbeat_payload() -> str:
    return json.dumps({"channel": "heartbeat"})


def make_message(seq: int, payload: str, *, session: int = 0, offset_ms: int = 0) -> RawMessage:
    """One recorded message at ``BASE_WALL_NS + offset_ms``."""
    return RawMessage(
        seq=seq,
        session=session,
        recv_ns=1_000_000 * (seq + 1),
        recv_wall_ns=BASE_WALL_NS + offset_ms * 1_000_000,
        payload=payload,
    )


@pytest.fixture
def messages() -> list[RawMessage]:
    """A short, well-formed session: status, ack, snapshot, updates, heartbeat."""
    payloads = [
        status_payload(),
        ack_payload(),
        snapshot_payload(),
        update_payload(63_999.9, 1.5),
        update_payload(64_000.1, 0.0, side="ask"),
        heartbeat_payload(),
        update_payload(63_999.8, 2.5),
    ]
    return [make_message(i, payload, offset_ms=i * 100) for i, payload in enumerate(payloads)]


@pytest.fixture
def recording(tmp_path: Path, messages: list[RawMessage]) -> Path:
    """``messages`` written to a real JSONL recording; returns the file path."""
    with JsonlRecorder(tmp_path / "raw", prefix="test") as recorder:
        for message in messages:
            recorder.write(message)
        paths = recorder.paths
    return paths[0]
