"""Replay recorded JSONL back into the same interface a live feed exposes.

Two modes, on purpose:

``iter_messages``
    Synchronous, unpaced, as fast as the disk allows. This is what tests and
    benchmarks use: no event loop, no sleeping, fully deterministic.
``stream``
    Asynchronous and paced, reproducing the original inter-arrival gaps (optionally
    time-compressed). This is what the rest of the system runs against when you want
    a realistic message cadence without a live socket.

Pacing is driven by ``recv_wall_ns``, not ``recv_ns``: the monotonic clock is only
meaningful inside the process that recorded it, whereas the wall clock stays
comparable across the reconnects and process restarts a long capture may contain.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

from l2tca.feed.types import RawMessage

log = logging.getLogger(__name__)


def resolve_paths(source: Path | str | Iterable[Path | str]) -> list[Path]:
    """Expand a file, a directory, or an explicit list into an ordered file list.

    Directories are expanded to their ``*.jsonl`` / ``*.jsonl.gz`` children. Files are
    sorted by name, which for the recorder's ``{prefix}_{YYYYMMDDTHH}`` naming is also
    chronological order.
    """
    if isinstance(source, (str, Path)):
        candidates: list[Path] = [Path(source)]
    else:
        candidates = [Path(p) for p in source]

    out: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir():
            out.extend(sorted(p for p in candidate.iterdir() if _is_jsonl(p)))
        else:
            out.append(candidate)
    missing = [p for p in out if not p.exists()]
    if missing:
        raise FileNotFoundError(f"no such recording: {', '.join(str(p) for p in missing)}")
    if not out:
        raise FileNotFoundError(f"no JSONL recordings found under {source}")
    return out


def _is_jsonl(path: Path) -> bool:
    return path.is_file() and (path.suffix == ".jsonl" or path.name.endswith(".jsonl.gz"))


def _open(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


class JsonlReplay:
    """Read back one or more recordings written by :class:`~l2tca.feed.recorder.JsonlRecorder`.

    Parameters
    ----------
    source:
        A file, a directory of recordings, or an explicit sequence of files.
    speed:
        Time compression for :meth:`stream`. ``1.0`` is real time, ``10.0`` is ten
        times faster, ``0`` (or ``inf``) disables pacing entirely.
    max_gap_s:
        Idle gaps longer than this are clamped during paced replay, so a capture that
        sat quiet over a reconnect does not stall the replay for minutes.
    limit:
        Stop after this many messages.
    strict:
        Raise on a malformed line (the default). When ``False``, log and skip it —
        useful for the tail of a file whose process was killed mid-write.
    """

    def __init__(
        self,
        source: Path | str | Iterable[Path | str],
        *,
        speed: float = 1.0,
        max_gap_s: float = 5.0,
        limit: int | None = None,
        strict: bool = True,
    ) -> None:
        if speed < 0:
            raise ValueError("speed must be >= 0 (0 means 'as fast as possible')")
        self.paths = resolve_paths(source)
        self.speed = speed
        self.max_gap_s = max_gap_s
        self.limit = limit
        self.strict = strict
        self._skipped = 0

    @property
    def skipped(self) -> int:
        """Malformed lines skipped (only ever non-zero when ``strict`` is False)."""
        return self._skipped

    def iter_messages(self) -> Iterator[RawMessage]:
        """Yield every recorded message as fast as possible, in recorded order."""
        emitted = 0
        for path in self.paths:
            with _open(path) as handle:
                for lineno, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = RawMessage.from_json(line)
                    except (ValueError, KeyError) as exc:
                        if self.strict:
                            raise ValueError(f"{path}:{lineno}: {exc}") from exc
                        self._skipped += 1
                        log.warning("skipping malformed record %s:%d (%s)", path, lineno, exc)
                        continue
                    yield message
                    emitted += 1
                    if self.limit is not None and emitted >= self.limit:
                        return

    async def stream(self) -> AsyncIterator[RawMessage]:
        """Yield messages paced to the recorded cadence, divided by ``speed``.

        Scheduling is absolute rather than per-message ``sleep(gap)``: each message
        is released at ``start + cumulative_virtual_gap``, so scheduler overshoot on
        one message does not accumulate into seconds of drift over a long replay.
        """
        paced = self.speed > 0 and self.speed != float("inf")
        start_ns = time.perf_counter_ns()
        virtual_elapsed_s = 0.0
        prev_wall_ns: int | None = None

        for message in self.iter_messages():
            if paced:
                if prev_wall_ns is not None:
                    gap_s = (message.recv_wall_ns - prev_wall_ns) / 1e9
                    gap_s = min(max(gap_s, 0.0), self.max_gap_s)
                    virtual_elapsed_s += gap_s / self.speed
                prev_wall_ns = message.recv_wall_ns
                target_ns = start_ns + int(virtual_elapsed_s * 1e9)
                delay_s = (target_ns - time.perf_counter_ns()) / 1e9
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
            yield message
