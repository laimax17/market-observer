"""Turn recorded frames into table rows.

The mapping is deliberately mechanical and lossless: a book frame carrying N level
entries becomes N ``tick`` rows, and a venue snapshot additionally becomes one
``snapshot`` row holding the whole image. Nothing here maintains book state — that
is :mod:`l2tca.book`'s job — so this module works on a raw recording alone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from l2tca.feed.parser import BookFrame, Frame, FrameDecodeError, parse_frame
from l2tca.feed.types import RawMessage
from l2tca.io.writer import ParquetPartitionWriter

log = logging.getLogger(__name__)


def decode(messages: Iterable[RawMessage]) -> Iterator[tuple[RawMessage, Frame]]:
    """Decode each recorded message, logging and skipping undecodable ones."""
    for message in messages:
        try:
            yield message, parse_frame(message.payload)
        except FrameDecodeError as exc:
            log.warning("skipping seq=%d: %s", message.seq, exc)


def tick_rows(message: RawMessage, frame: BookFrame) -> list[dict[str, Any]]:
    """One row per published level entry, bids then asks."""
    base = {
        "session": message.session,
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "exchange_ts_ns": frame.exchange_ts_ns,
        "symbol": frame.symbol,
        "is_snapshot": frame.is_snapshot,
        "checksum": frame.checksum,
    }
    return [
        {**base, "side": side, "price": level.price, "qty": level.qty}
        for side, levels in (("bid", frame.bids), ("ask", frame.asks))
        for level in levels
    ]


def snapshot_row(message: RawMessage, frame: BookFrame, *, source: str = "venue") -> dict[str, Any]:
    """One row holding a full book image.

    ``source`` distinguishes an image the venue sent from one taken later off
    reconstructed state, because only the former is trustworthy on its own.
    """
    return {
        "session": message.session,
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "exchange_ts_ns": frame.exchange_ts_ns,
        "symbol": frame.symbol,
        "depth": max(len(frame.bids), len(frame.asks)),
        "bids": [{"price": level.price, "qty": level.qty} for level in frame.bids],
        "asks": [{"price": level.price, "qty": level.qty} for level in frame.asks],
        "checksum": frame.checksum,
        "source": source,
    }


def signal_row(message: RawMessage, symbol: str, name: str, value: float | None) -> dict[str, Any]:
    """One factor observation, keyed to the book message that produced it."""
    return {
        "session": message.session,
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "symbol": symbol,
        "name": name,
        "value": value,
    }


@dataclass(slots=True)
class ExportReport:
    """What an export run actually did — printed by ``l2tca export``."""

    messages: int = 0
    tick_rows: int = 0
    snapshot_rows: int = 0
    skipped: int = 0
    frame_counts: dict[str, int] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)

    def describe(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.frame_counts.items()))
        return (
            f"messages={self.messages} tick_rows={self.tick_rows} "
            f"snapshot_rows={self.snapshot_rows} skipped={self.skipped}\n"
            f"frames: {counts}\n"
            f"files: {len(self.files)}"
        )


def export_to_parquet(
    messages: Iterable[RawMessage],
    root: Path | str,
    *,
    row_group_size: int = 50_000,
) -> ExportReport:
    """Write a recording's book frames into the ``tick`` and ``snapshot`` tables."""
    report = ExportReport()
    tick_writer = ParquetPartitionWriter(root, "tick", row_group_size=row_group_size)
    snapshot_writer = ParquetPartitionWriter(root, "snapshot", row_group_size=1_000)
    try:
        for message, frame in decode(messages):
            report.messages += 1
            name = type(frame).__name__
            report.frame_counts[name] = report.frame_counts.get(name, 0) + 1
            if not isinstance(frame, BookFrame):
                continue
            rows = tick_rows(message, frame)
            tick_writer.write_rows(rows)
            report.tick_rows += len(rows)
            if frame.is_snapshot:
                snapshot_writer.write_rows([snapshot_row(message, frame)])
                report.snapshot_rows += 1
    finally:
        tick_writer.close()
        snapshot_writer.close()
    report.files = tick_writer.files + snapshot_writer.files
    return report
