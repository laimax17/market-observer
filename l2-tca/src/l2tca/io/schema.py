"""Arrow schemas for the three persisted tables.

Every table carries ``schema_version`` as a column, not just as file metadata. A
reader that finds an unexpected version can then fail loudly *per row group* while
still being able to read a mixed-version directory — which is what actually happens
when a schema changes halfway through a capture day.

Layout on disk (hive-style, so Polars and DuckDB both discover the partitions)::

    data/parquet/tick/date=2026-08-27/hour=14/part-0000.parquet
    data/parquet/snapshot/date=2026-08-27/hour=14/part-0000.parquet
    data/parquet/signal/date=2026-08-27/hour=14/part-0000.parquet
"""

from __future__ import annotations

import pyarrow as pa

#: Bump on any incompatible change to the table definitions below.
SCHEMA_VERSION = 1

_SYMBOL = pa.dictionary(pa.int32(), pa.string())
_SIDE = pa.dictionary(pa.int8(), pa.string())
_NAME = pa.dictionary(pa.int32(), pa.string())

#: A published level change: one row per (side, price) entry in a book frame.
#: Snapshot rows are included and flagged, so a day can be rebuilt from this table
#: alone without going back to the JSONL.
TICK_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.int16(), nullable=False),
        pa.field("session", pa.int32(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("recv_ns", pa.int64(), nullable=False),
        pa.field("recv_wall_ns", pa.int64(), nullable=False),
        pa.field("exchange_ts_ns", pa.int64(), nullable=True),
        pa.field("symbol", _SYMBOL, nullable=False),
        pa.field("side", _SIDE, nullable=False),
        pa.field("price", pa.float64(), nullable=False),
        # qty == 0 is a deletion of that level, not a zero-size order.
        pa.field("qty", pa.float64(), nullable=False),
        pa.field("is_snapshot", pa.bool_(), nullable=False),
        pa.field("checksum", pa.uint32(), nullable=True),
    ],
    metadata={"l2tca.table": "tick", "l2tca.schema_version": str(SCHEMA_VERSION)},
)

_LEVEL = pa.struct([pa.field("price", pa.float64()), pa.field("qty", pa.float64())])

#: A full book image: the venue's own snapshot, or one taken from reconstructed
#: state on a fixed interval so a backtest can start mid-file.
SNAPSHOT_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.int16(), nullable=False),
        pa.field("session", pa.int32(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("recv_ns", pa.int64(), nullable=False),
        pa.field("recv_wall_ns", pa.int64(), nullable=False),
        pa.field("exchange_ts_ns", pa.int64(), nullable=True),
        pa.field("symbol", _SYMBOL, nullable=False),
        pa.field("depth", pa.int32(), nullable=False),
        pa.field("bids", pa.list_(_LEVEL), nullable=False),
        pa.field("asks", pa.list_(_LEVEL), nullable=False),
        pa.field("checksum", pa.uint32(), nullable=True),
        pa.field("source", _NAME, nullable=False),  # "venue" or "reconstructed"
    ],
    metadata={"l2tca.table": "snapshot", "l2tca.schema_version": str(SCHEMA_VERSION)},
)

#: Long format (one row per factor value) rather than one column per factor: adding
#: a factor must not require a schema migration of the historical archive.
SIGNAL_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.int16(), nullable=False),
        pa.field("session", pa.int32(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),  # book message that produced it
        pa.field("recv_ns", pa.int64(), nullable=False),
        pa.field("recv_wall_ns", pa.int64(), nullable=False),
        pa.field("symbol", _SYMBOL, nullable=False),
        pa.field("name", _NAME, nullable=False),
        pa.field("value", pa.float64(), nullable=True),  # None where undefined
    ],
    metadata={"l2tca.table": "signal", "l2tca.schema_version": str(SCHEMA_VERSION)},
)

TABLES: dict[str, pa.Schema] = {
    "tick": TICK_SCHEMA,
    "snapshot": SNAPSHOT_SCHEMA,
    "signal": SIGNAL_SCHEMA,
}


def schema_for(table: str) -> pa.Schema:
    """Look up a table schema by name, with a helpful error for a typo."""
    try:
        return TABLES[table]
    except KeyError:
        raise KeyError(f"unknown table {table!r}; known tables: {sorted(TABLES)}") from None


def empty_table(table: str) -> pa.Table:
    """An empty Arrow table with the right schema — handy in tests and as a seed."""
    return schema_for(table).empty_table()


def rows_to_table(table: str, rows: list[dict]) -> pa.Table:
    """Build an Arrow table from row dicts, filling ``schema_version`` if absent."""
    schema = schema_for(table)
    prepared = [{"schema_version": SCHEMA_VERSION, **row} for row in rows]
    return pa.Table.from_pylist(prepared, schema=schema)
