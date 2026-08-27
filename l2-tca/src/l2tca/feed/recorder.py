"""Raw JSONL recording — the highest-value piece of this project.

Everything downstream (book, signals, TCA, benchmarks) is developed against
recorded files rather than a live socket, so development is offline, repeatable and
deterministic. The rule the recorder follows: *store the bytes the venue sent, plus
the receive stamps, and nothing else.* No normalisation, no re-serialisation of the
payload, no dropped fields — a decoder bug must be fixable by re-running the decoder
over yesterday's file rather than by capturing a fresh day.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from l2tca.feed.types import RawMessage

log = logging.getLogger(__name__)


def hour_key(wall_ns: int) -> str:
    """UTC hour bucket for a wall-clock nanosecond stamp, e.g. ``20260827T14``."""
    dt = datetime.fromtimestamp(wall_ns / 1e9, tz=UTC)
    return dt.strftime("%Y%m%dT%H")


class JsonlRecorder:
    """Append :class:`RawMessage` records to hour-partitioned JSONL files.

    Files are named ``{prefix}_{YYYYMMDDTHH}.jsonl`` and rotate on the UTC hour, so a
    long run produces bounded files that line up with the Parquet hour partitions.

    ``line_buffered`` (the default) trades a little throughput for crash safety: if
    the process is killed, everything written up to the last message survives. For a
    100-level book on one pair this costs nothing measurable; turn it off for a
    high-rate multi-symbol capture.
    """

    def __init__(
        self,
        out_dir: Path | str,
        *,
        prefix: str = "kraken_book",
        line_buffered: bool = True,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.prefix = prefix
        self.line_buffered = line_buffered
        self._handle = None
        self._current_hour: str | None = None
        self._written = 0
        self._paths: list[Path] = []

    @property
    def written(self) -> int:
        """Messages written since construction."""
        return self._written

    @property
    def paths(self) -> list[Path]:
        """Files this recorder has opened, in order."""
        return list(self._paths)

    def path_for(self, wall_ns: int) -> Path:
        return self.out_dir / f"{self.prefix}_{hour_key(wall_ns)}.jsonl"

    def write(self, message: RawMessage) -> None:
        """Append one record, rotating the file if the UTC hour has ticked over."""
        hour = hour_key(message.recv_wall_ns)
        if hour != self._current_hour:
            self._rotate(message.recv_wall_ns, hour)
        assert self._handle is not None
        self._handle.write(message.to_json())
        self._handle.write("\n")
        self._written += 1

    def _rotate(self, wall_ns: int, hour: str) -> None:
        self.close()
        path = self.path_for(wall_ns)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8", buffering=1 if self.line_buffered else -1)
        self._current_hour = hour
        self._paths.append(path)
        log.info("recording to %s", path)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None
        self._current_hour = None

    def __enter__(self) -> JsonlRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


async def record_stream(
    messages: AsyncIterator[RawMessage],
    recorder: JsonlRecorder,
    *,
    max_messages: int | None = None,
    on_message: object | None = None,
) -> int:
    """Drain ``messages`` into ``recorder``; return the number recorded.

    ``on_message`` is an optional callable invoked with each message (progress
    reporting, live decoding) — it runs *after* the write, so a slow observer can
    never cost the recording a message.
    """
    count = 0
    try:
        async for message in messages:
            recorder.write(message)
            count += 1
            if callable(on_message):
                on_message(message)
            if max_messages is not None and count >= max_messages:
                break
    finally:
        recorder.close()
    return count
