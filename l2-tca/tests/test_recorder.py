"""Recording must be lossless, ordered, hour-partitioned and crash-tolerant."""

from __future__ import annotations

import contextlib
import json

from l2tca.feed.recorder import JsonlRecorder, hour_key, record_stream
from l2tca.feed.replay import JsonlReplay
from l2tca.feed.types import RawMessage
from tests.conftest import BASE_WALL_NS, heartbeat_payload, make_message

HOUR_NS = 3_600_000_000_000


def test_round_trips_through_a_real_file(tmp_path, messages):
    with JsonlRecorder(tmp_path, prefix="test") as recorder:
        for message in messages:
            recorder.write(message)
        assert recorder.written == len(messages)

    assert list(JsonlReplay(tmp_path).iter_messages()) == messages


def test_files_rotate_on_the_utc_hour(tmp_path):
    spanning = [
        make_message(0, heartbeat_payload()),
        RawMessage(1, 0, 2, BASE_WALL_NS + HOUR_NS, heartbeat_payload()),
        RawMessage(2, 0, 3, BASE_WALL_NS + 2 * HOUR_NS, heartbeat_payload()),
    ]
    with JsonlRecorder(tmp_path, prefix="test") as recorder:
        for message in spanning:
            recorder.write(message)
        paths = recorder.paths

    assert [p.name for p in paths] == [
        "test_20260827T14.jsonl",
        "test_20260827T15.jsonl",
        "test_20260827T16.jsonl",
    ]
    assert all(len(p.read_text().splitlines()) == 1 for p in paths)


def test_hour_key_is_utc():
    assert hour_key(BASE_WALL_NS) == "20260827T14"


def test_appending_to_an_existing_hour_does_not_truncate(tmp_path, messages):
    for _ in range(2):
        with JsonlRecorder(tmp_path, prefix="test") as recorder:
            for message in messages:
                recorder.write(message)

    path = tmp_path / "test_20260827T14.jsonl"
    assert len(path.read_text().splitlines()) == 2 * len(messages)


def test_line_buffering_means_a_killed_process_keeps_what_it_wrote(tmp_path, messages):
    recorder = JsonlRecorder(tmp_path, prefix="test")  # deliberately never closed
    for message in messages:
        recorder.write(message)

    path = tmp_path / "test_20260827T14.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == len(messages)
    assert json.loads(lines[0])["seq"] == 0


async def test_record_stream_honours_max_messages(tmp_path, messages):
    async def source():
        for message in messages:
            yield message

    recorder = JsonlRecorder(tmp_path, prefix="test")
    seen: list[int] = []
    count = await record_stream(
        source(), recorder, max_messages=3, on_message=lambda m: seen.append(m.seq)
    )

    assert count == 3
    assert seen == [0, 1, 2]
    assert len((tmp_path / "test_20260827T14.jsonl").read_text().splitlines()) == 3


async def test_record_stream_closes_the_file_even_when_the_feed_raises(tmp_path, messages):
    async def failing_source():
        yield messages[0]
        raise ConnectionError("venue went away")

    recorder = JsonlRecorder(tmp_path, prefix="test")
    with contextlib.suppress(ConnectionError):
        await record_stream(failing_source(), recorder)

    assert (tmp_path / "test_20260827T14.jsonl").read_text().splitlines()
