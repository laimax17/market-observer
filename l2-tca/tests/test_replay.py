"""Replay: deterministic order, honest pacing, and tolerance for truncated files."""

from __future__ import annotations

import gzip
import time

import pytest

from l2tca.feed.replay import JsonlReplay, resolve_paths
from l2tca.feed.types import RawMessage
from tests.conftest import BASE_WALL_NS, heartbeat_payload


def write_jsonl(path, count, *, gap_ms=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for i in range(count):
            message = RawMessage(
                seq=i,
                session=0,
                recv_ns=i * gap_ms * 1_000_000,
                recv_wall_ns=BASE_WALL_NS + i * gap_ms * 1_000_000,
                payload=heartbeat_payload(),
            )
            handle.write(message.to_json() + "\n")
    return path


def test_iter_messages_preserves_order(recording, messages):
    assert list(JsonlReplay(recording).iter_messages()) == messages


def test_limit_stops_early(recording):
    assert len(list(JsonlReplay(recording, limit=2).iter_messages())) == 2


def test_a_directory_is_read_in_chronological_file_order(tmp_path):
    write_jsonl(tmp_path / "raw" / "test_20260827T15.jsonl", 2)
    write_jsonl(tmp_path / "raw" / "test_20260827T14.jsonl", 3)

    paths = resolve_paths(tmp_path / "raw")
    assert [p.name for p in paths] == ["test_20260827T14.jsonl", "test_20260827T15.jsonl"]
    assert len(list(JsonlReplay(tmp_path / "raw").iter_messages())) == 5


def test_gzipped_recordings_are_read_transparently(tmp_path, recording):
    archive = tmp_path / "archive.jsonl.gz"
    archive.write_bytes(gzip.compress(recording.read_bytes()))
    assert list(JsonlReplay(archive).iter_messages()) == list(
        JsonlReplay(recording).iter_messages()
    )


def test_a_truncated_line_raises_by_default(tmp_path):
    path = write_jsonl(tmp_path / "raw.jsonl", 3)
    with path.open("a") as handle:
        handle.write('{"v":1,"seq":3,"session":0,"recv_ns"')  # killed mid-write

    with pytest.raises(ValueError, match=r"raw\.jsonl:4"):
        list(JsonlReplay(path).iter_messages())


def test_lenient_mode_skips_the_truncated_tail(tmp_path):
    path = write_jsonl(tmp_path / "raw.jsonl", 3)
    with path.open("a") as handle:
        handle.write('{"v":1,"seq":3,"session":0,"recv_ns"')

    replay = JsonlReplay(path, strict=False)
    assert len(list(replay.iter_messages())) == 3
    assert replay.skipped == 1


def test_missing_path_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonlReplay(tmp_path / "nope.jsonl")


async def test_paced_replay_reproduces_the_recorded_gaps(tmp_path):
    path = write_jsonl(tmp_path / "raw.jsonl", 6, gap_ms=20)  # 100ms of market time

    start = time.perf_counter()
    seen = [m.seq async for m in JsonlReplay(path, speed=1.0).stream()]
    elapsed = time.perf_counter() - start

    assert seen == list(range(6))
    assert 0.08 <= elapsed <= 0.5, f"expected ~0.1s of pacing, took {elapsed:.3f}s"


async def test_speed_compresses_time(tmp_path):
    path = write_jsonl(tmp_path / "raw.jsonl", 11, gap_ms=100)  # 1s of market time

    start = time.perf_counter()
    async for _ in JsonlReplay(path, speed=20.0).stream():
        pass
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5, f"20x replay of 1s took {elapsed:.3f}s"


async def test_speed_zero_disables_pacing(tmp_path):
    path = write_jsonl(tmp_path / "raw.jsonl", 50, gap_ms=1000)  # 49s of market time

    start = time.perf_counter()
    seen = [m async for m in JsonlReplay(path, speed=0).stream()]
    assert len(seen) == 50
    assert time.perf_counter() - start < 0.5


async def test_long_idle_gaps_are_clamped(tmp_path):
    # A reconnect leaves a minutes-long hole in the wall clock; a paced replay must
    # not sit through it.
    path = tmp_path / "raw.jsonl"
    with path.open("w") as handle:
        for i, offset_ns in enumerate([0, 10_000_000, 600 * 1_000_000_000]):
            handle.write(
                RawMessage(i, 0, offset_ns, BASE_WALL_NS + offset_ns, heartbeat_payload()).to_json()
                + "\n"
            )

    start = time.perf_counter()
    seen = [m async for m in JsonlReplay(path, speed=1.0, max_gap_s=0.05).stream()]
    assert len(seen) == 3
    assert time.perf_counter() - start < 1.0


def test_negative_speed_is_rejected(recording):
    with pytest.raises(ValueError, match="speed"):
        JsonlReplay(recording, speed=-1.0)
