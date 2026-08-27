"""Frame decoding: every modelled shape, and graceful behaviour on the rest."""

from __future__ import annotations

import json

import pytest

from l2tca.feed.parser import (
    BookFrame,
    ErrorFrame,
    FrameDecodeError,
    HeartbeatFrame,
    StatusFrame,
    SubscribeAck,
    UnknownFrame,
    parse_frame,
    parse_timestamp_ns,
)
from tests.conftest import (
    ack_payload,
    heartbeat_payload,
    snapshot_payload,
    status_payload,
    update_payload,
)


def test_status_frame():
    frame = parse_frame(status_payload())
    assert isinstance(frame, StatusFrame)
    assert frame.system == "online"
    assert frame.api_version == "v2"
    assert frame.connection_id == 12345678901234


def test_subscribe_ack():
    frame = parse_frame(ack_payload())
    assert isinstance(frame, SubscribeAck)
    assert frame.success is True
    assert frame.req_id == 1


def test_subscribe_rejection_carries_the_reason():
    frame = parse_frame(ack_payload(success=False))
    assert isinstance(frame, SubscribeAck)
    assert frame.success is False
    assert frame.error == "Subscription Not Found"


def test_heartbeat():
    assert isinstance(parse_frame(heartbeat_payload()), HeartbeatFrame)


def test_snapshot_frame_levels_are_ordered_as_published():
    frame = parse_frame(snapshot_payload(levels=3))
    assert isinstance(frame, BookFrame)
    assert frame.is_snapshot is True
    assert len(frame.bids) == 3 and len(frame.asks) == 3
    assert frame.bids[0].price > frame.bids[1].price
    assert frame.asks[0].price < frame.asks[1].price
    assert frame.checksum == 1234567890
    assert frame.exchange_ts_ns is None  # snapshots carry no timestamp


def test_update_frame_keeps_zero_qty_deletions():
    frame = parse_frame(update_payload(64_000.1, 0.0, side="ask"))
    assert isinstance(frame, BookFrame)
    assert frame.is_snapshot is False
    assert frame.bids == ()
    assert frame.asks[0].qty == 0.0
    assert frame.exchange_ts_ns is not None


def test_error_frame():
    frame = parse_frame(json.dumps({"error": "Rate limit exceeded", "req_id": 3}))
    assert isinstance(frame, ErrorFrame)
    assert frame.error == "Rate limit exceeded"


def test_unmodelled_channel_survives_as_unknown():
    # A venue adding a channel must never take a recording down.
    frame = parse_frame(json.dumps({"channel": "trade", "type": "update", "data": []}))
    assert isinstance(frame, UnknownFrame)
    assert frame.raw["channel"] == "trade"


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[1,2,3]",
        '{"channel":"book","data":[]}',
        '{"channel":"book","data":[{"bids":5}]}',
    ],
)
def test_malformed_frames_raise(payload):
    with pytest.raises(FrameDecodeError):
        parse_frame(payload)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-08-27T14:30:01.123456Z", 1787841001_123456000),
        ("2026-08-27T14:30:01.123Z", 1787841001_123000000),
        ("2026-08-27T14:30:01Z", 1787841001_000000000),
        ("2026-08-27T14:30:01.123456789Z", 1787841001_123456000),  # ns truncated to us
    ],
)
def test_timestamp_parsing(text, expected):
    assert parse_timestamp_ns(text) == expected


@pytest.mark.parametrize("text", [None, "", "not a timestamp"])
def test_bad_timestamps_degrade_to_none(text):
    assert parse_timestamp_ns(text) is None
