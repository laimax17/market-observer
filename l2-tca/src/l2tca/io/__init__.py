"""Parquet storage: schemas, hour-partitioned writing, and read-back validation."""

from l2tca.io.convert import (
    ExportReport,
    export_to_parquet,
    signal_row,
    snapshot_row,
    tick_rows,
)
from l2tca.io.reader import ValidationReport, read_table, scan_table, validate_table
from l2tca.io.schema import (
    SCHEMA_VERSION,
    SIGNAL_SCHEMA,
    SNAPSHOT_SCHEMA,
    TABLES,
    TICK_SCHEMA,
    empty_table,
    rows_to_table,
    schema_for,
)
from l2tca.io.writer import ParquetPartitionWriter, partition_key

__all__ = [
    "SCHEMA_VERSION",
    "SIGNAL_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "TABLES",
    "TICK_SCHEMA",
    "ExportReport",
    "ParquetPartitionWriter",
    "ValidationReport",
    "empty_table",
    "export_to_parquet",
    "partition_key",
    "read_table",
    "rows_to_table",
    "scan_table",
    "schema_for",
    "signal_row",
    "snapshot_row",
    "tick_rows",
    "validate_table",
]
