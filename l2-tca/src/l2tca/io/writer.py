"""Hour-partitioned Parquet writing.

One open :class:`pyarrow.parquet.ParquetWriter` per live partition, buffered in
memory and flushed as row groups. Two properties matter for this use case:

* **Bounded memory.** The buffer flushes every ``row_group_size`` rows, so a
  full trading day never has to fit in RAM.
* **Bounded file size.** Partitions roll to ``part-0001``, ``part-0002``, ... once
  ``rows_per_file`` is reached, which keeps a single corrupt file from costing more
  than a few minutes of data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from l2tca.io.schema import SCHEMA_VERSION, schema_for

log = logging.getLogger(__name__)


def partition_key(wall_ns: int) -> tuple[str, str]:
    """UTC ``(date, hour)`` partition values for a wall-clock nanosecond stamp."""
    dt = datetime.fromtimestamp(wall_ns / 1e9, tz=UTC)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H")


@dataclass(slots=True)
class _Partition:
    directory: Path
    file_index: int = 0
    rows_in_file: int = 0
    buffer: list[dict[str, Any]] = field(default_factory=list)
    writer: pq.ParquetWriter | None = None

    @property
    def path(self) -> Path:
        return self.directory / f"part-{self.file_index:04d}.parquet"


class ParquetPartitionWriter:
    """Append rows to ``{root}/{table}/date=.../hour=.../part-NNNN.parquet``.

    The writer is intentionally dumb about *what* the rows mean: it validates them
    against the table schema (Arrow raises on a missing or mistyped field) and
    routes them by ``recv_wall_ns``. Deciding what a row is belongs in
    :mod:`l2tca.io.convert`.
    """

    def __init__(
        self,
        root: Path | str,
        table: str,
        *,
        row_group_size: int = 50_000,
        rows_per_file: int = 2_000_000,
        compression: str = "zstd",
    ) -> None:
        self.root = Path(root)
        self.table = table
        self.schema = schema_for(table)
        self.row_group_size = row_group_size
        self.rows_per_file = rows_per_file
        self.compression = compression
        self._partitions: dict[tuple[str, str], _Partition] = {}
        self._rows_written = 0
        self._files: list[Path] = []

    @property
    def rows_written(self) -> int:
        """Rows handed to :meth:`write_rows` (buffered rows included)."""
        return self._rows_written

    @property
    def files(self) -> list[Path]:
        """Parquet files created so far, in creation order."""
        return list(self._files)

    def write_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """Buffer rows, flushing whole row groups as they fill.

        Rows may span partitions and arrive in any order; each is routed to the
        partition its ``recv_wall_ns`` falls in.
        """
        for row in rows:
            key = partition_key(int(row["recv_wall_ns"]))
            partition = self._partitions.get(key)
            if partition is None:
                directory = self.root / self.table / f"date={key[0]}" / f"hour={key[1]}"
                directory.mkdir(parents=True, exist_ok=True)
                partition = _Partition(directory=directory)
                self._partitions[key] = partition
            partition.buffer.append({"schema_version": SCHEMA_VERSION, **dict(row)})
            self._rows_written += 1
            if len(partition.buffer) >= self.row_group_size:
                self._flush(partition)

    def flush(self) -> None:
        """Write every buffered row out as a row group."""
        for partition in self._partitions.values():
            self._flush(partition)

    def _flush(self, partition: _Partition) -> None:
        if not partition.buffer:
            return
        batch = pa.Table.from_pylist(partition.buffer, schema=self.schema)
        partition.buffer.clear()

        if partition.writer is not None and partition.rows_in_file >= self.rows_per_file:
            partition.writer.close()
            partition.writer = None
            partition.file_index += 1
            partition.rows_in_file = 0

        if partition.writer is None:
            path = partition.path
            partition.writer = pq.ParquetWriter(path, self.schema, compression=self.compression)
            self._files.append(path)
            log.info("writing %s", path)

        partition.writer.write_table(batch)
        partition.rows_in_file += batch.num_rows

    def close(self) -> None:
        """Flush everything and close every open file. Safe to call twice."""
        for partition in self._partitions.values():
            self._flush(partition)
            if partition.writer is not None:
                partition.writer.close()
                partition.writer = None
        self._partitions.clear()

    def __enter__(self) -> ParquetPartitionWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
