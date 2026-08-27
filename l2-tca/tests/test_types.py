"""RawMessage is the on-disk contract: it must round-trip byte for byte."""

from __future__ import annotations

import json

import pytest

from l2tca.feed.types import RECORD_VERSION, RawMessage


def test_round_trip_preserves_every_field(messages):
    for message in messages:
        assert RawMessage.from_json(message.to_json()) == message


def test_payload_is_stored_verbatim():
    # Awkward but legal JSON: unusual spacing, unicode, a very long float. A recorder
    # that re-serialised the payload would quietly normalise all three.
    payload = '{"a":  1,"b":"\\u00e9","c":0.10000000000000000555}'
    message = RawMessage(seq=0, session=0, recv_ns=1, recv_wall_ns=2, payload=payload)
    assert RawMessage.from_json(message.to_json()).payload == payload


def test_line_is_single_line_json(messages):
    line = messages[0].to_json()
    assert "\n" not in line
    assert json.loads(line)["v"] == RECORD_VERSION


def test_unknown_record_version_is_rejected():
    line = json.dumps(
        {"v": 99, "seq": 0, "session": 0, "recv_ns": 1, "recv_wall_ns": 2, "payload": "{}"}
    )
    with pytest.raises(ValueError, match="unsupported raw record version"):
        RawMessage.from_json(line)
