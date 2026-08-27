"""Read the Parquet tables back with Polars and check they are usable.

Writing data is only half of a storage layer; the half that catches bugs is reading
it back and asserting the invariants. :func:`validate_table` is what CI runs after a
capture, and what ``l2tca inspect`` prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from l2tca.io.schema import SCHEMA_VERSION, schema_for


def table_root(root: Path | str, table: str) -> Path:
    return Path(root) / table


def scan_table(root: Path | str, table: str) -> pl.LazyFrame:
    """Lazily scan every partition of a table, with ``date``/``hour`` as columns."""
    directory = table_root(root, table)
    if not directory.exists():
        raise FileNotFoundError(f"no {table!r} table under {root}")
    return pl.scan_parquet(directory / "**" / "*.parquet", hive_partitioning=True)


def read_table(root: Path | str, table: str) -> pl.DataFrame:
    """Read a whole table into memory. Fine for a session; not for a month."""
    return scan_table(root, table).collect()


@dataclass(slots=True)
class ValidationReport:
    """Result of :func:`validate_table`. Falsy ``errors`` means the table is sound."""

    table: str
    files: int = 0
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    schema_versions: list[int] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    sessions: list[int] = field(default_factory=list)
    partitions: list[str] = field(default_factory=list)
    first_wall_ns: int | None = None
    last_wall_ns: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def describe(self) -> str:
        def stamp(ns: int | None) -> str:
            if ns is None:
                return "-"
            return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat(timespec="milliseconds")

        lines = [
            f"table          {self.table}",
            f"files          {self.files}",
            f"rows           {self.rows}",
            f"partitions     {len(self.partitions)} ({', '.join(self.partitions[:6])}"
            + (", ..." if len(self.partitions) > 6 else "")
            + ")",
            f"schema version {self.schema_versions}",
            f"symbols        {self.symbols}",
            f"sessions       {self.sessions}",
            f"time range     {stamp(self.first_wall_ns)} .. {stamp(self.last_wall_ns)}",
            f"status         {'OK' if self.ok else 'FAILED'}",
        ]
        lines.extend(f"  error: {e}" for e in self.errors)
        return "\n".join(lines)


def validate_table(root: Path | str, table: str) -> ValidationReport:
    """Read a table back and assert the invariants that make it usable.

    Checks, in order of how often each one has actually caught something:

    1. The files exist and parse.
    2. Columns match the declared schema exactly (no silently dropped field).
    3. ``schema_version`` is present and known.
    4. Columns declared non-nullable really carry no nulls.
    5. ``recv_wall_ns`` is non-decreasing within a session, and ``seq`` is
       non-decreasing (one book message fans out into several rows, so equal
       consecutive values are expected — a *decrease* is not).
    6. Every row landed in the hour partition its own timestamp implies.
    """
    report = ValidationReport(table=table)
    directory = table_root(root, table)
    files = sorted(directory.rglob("*.parquet")) if directory.exists() else []
    report.files = len(files)
    if not files:
        report.errors.append(f"no parquet files under {directory}")
        return report

    frame = read_table(root, table)
    report.rows = frame.height
    report.columns = list(frame.columns)
    report.partitions = sorted(
        {f"{d}T{h}" for d, h in zip(frame["date"].to_list(), frame["hour"].to_list(), strict=True)}
    )

    expected = set(schema_for(table).names)
    missing = expected - set(frame.columns)
    if missing:
        report.errors.append(f"missing columns: {sorted(missing)}")
    unexpected = set(frame.columns) - expected - {"date", "hour"}
    if unexpected:
        report.errors.append(f"unexpected columns: {sorted(unexpected)}")

    if "schema_version" in frame.columns:
        report.schema_versions = sorted(frame["schema_version"].unique().to_list())
        unknown = [v for v in report.schema_versions if v != SCHEMA_VERSION]
        if unknown:
            report.errors.append(
                f"unreadable schema versions {unknown} (this build writes v{SCHEMA_VERSION})"
            )

    for name in (f.name for f in schema_for(table) if not f.nullable):
        if name in frame.columns and frame[name].null_count():
            report.errors.append(f"{name} has {frame[name].null_count()} nulls but is non-nullable")

    if "symbol" in frame.columns:
        report.symbols = sorted(frame["symbol"].unique().to_list())
    if "session" in frame.columns:
        report.sessions = sorted(frame["session"].unique().to_list())

    if "recv_wall_ns" in frame.columns and frame.height:
        report.first_wall_ns = int(frame["recv_wall_ns"].min())
        report.last_wall_ns = int(frame["recv_wall_ns"].max())
        by_session = frame.sort(["session", "seq"])
        for column in ("recv_wall_ns", "seq"):
            if column not in frame.columns:
                continue
            regressions = (
                by_session.with_columns(pl.col(column).diff().over("session").alias("_delta"))
                .filter(pl.col("_delta") < 0)
                .height
            )
            if regressions:
                report.errors.append(f"{column} decreases {regressions} times within a session")

        wrong_partition = (
            frame.with_columns(
                pl.from_epoch(pl.col("recv_wall_ns"), time_unit="ns")
                .dt.replace_time_zone("UTC")
                .alias("_ts")
            )
            .with_columns(
                _date=pl.col("_ts").dt.strftime("%Y-%m-%d"),
                _hour=pl.col("_ts").dt.strftime("%H"),
            )
            .filter(
                (pl.col("_date") != pl.col("date").cast(pl.String))
                | (pl.col("_hour") != pl.col("hour").cast(pl.String).str.zfill(2))
            )
            .height
        )
        if wrong_partition:
            report.errors.append(f"{wrong_partition} rows are in the wrong hour partition")

    return report
