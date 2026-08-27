"""Storage layer: schemas, partitioning, read-back and the validation that guards them."""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from l2tca.feed.parser import BookFrame, parse_frame
from l2tca.io.convert import export_to_parquet, signal_row, snapshot_row, tick_rows
from l2tca.io.reader import read_table, scan_table, validate_table
from l2tca.io.schema import SCHEMA_VERSION, TABLES, empty_table, rows_to_table, schema_for
from l2tca.io.writer import ParquetPartitionWriter, partition_key
from tests.conftest import BASE_WALL_NS, make_message, snapshot_payload, update_payload

HOUR_NS = 3_600_000_000_000


def tick(seq: int, *, wall_ns: int = BASE_WALL_NS, side: str = "bid", price: float = 64_000.0):
    return {
        "session": 0,
        "seq": seq,
        "recv_ns": seq * 1_000,
        "recv_wall_ns": wall_ns,
        "exchange_ts_ns": None,
        "symbol": "BTC/USD",
        "side": side,
        "price": price,
        "qty": 1.0,
        "is_snapshot": False,
        "checksum": 42,
    }


# ------------------------------------------------------------------- schemas


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_table_carries_a_schema_version_column(table):
    assert "schema_version" in schema_for(table).names
    assert empty_table(table).num_rows == 0


def test_unknown_table_names_fail_loudly():
    with pytest.raises(KeyError, match="unknown table"):
        schema_for("trades")


def test_rows_to_table_fills_the_schema_version():
    table = rows_to_table("tick", [tick(0)])
    assert table.column("schema_version").to_pylist() == [SCHEMA_VERSION]


def test_a_mistyped_row_is_rejected_rather_than_coerced():
    bad = tick(0) | {"price": "not a number"}
    with pytest.raises(pa.ArrowInvalid):
        rows_to_table("tick", [bad])


# ------------------------------------------------------------------ writing


def test_partition_key_is_the_utc_hour():
    assert partition_key(BASE_WALL_NS) == ("2026-08-27", "14")


def test_rows_land_in_hive_partitions_by_hour(tmp_path):
    with ParquetPartitionWriter(tmp_path, "tick") as writer:
        writer.write_rows([tick(0), tick(1, wall_ns=BASE_WALL_NS + HOUR_NS)])

    assert (tmp_path / "tick" / "date=2026-08-27" / "hour=14" / "part-0000.parquet").exists()
    assert (tmp_path / "tick" / "date=2026-08-27" / "hour=15" / "part-0000.parquet").exists()


def test_interleaved_partitions_stay_open(tmp_path):
    # Rows can arrive for hour 15 and then hour 14 again (a late batch, a replay of a
    # second session). Both partition writers must stay usable rather than one
    # truncating the other.
    with ParquetPartitionWriter(tmp_path, "tick") as writer:
        writer.write_rows([tick(0, wall_ns=BASE_WALL_NS + HOUR_NS)])
        writer.write_rows([tick(1)])
        writer.write_rows([tick(2, wall_ns=BASE_WALL_NS + HOUR_NS)])

    frame = read_table(tmp_path, "tick")
    assert frame.height == 3
    assert sorted(frame["hour"].cast(pl.String).to_list()) == ["14", "15", "15"]


def test_files_roll_once_rows_per_file_is_reached(tmp_path):
    with ParquetPartitionWriter(tmp_path, "tick", row_group_size=2, rows_per_file=4) as writer:
        writer.write_rows([tick(i) for i in range(10)])

    parts = sorted((tmp_path / "tick" / "date=2026-08-27" / "hour=14").iterdir())
    assert [p.name for p in parts] == [
        "part-0000.parquet",
        "part-0001.parquet",
        "part-0002.parquet",
    ]
    assert read_table(tmp_path, "tick").height == 10


def test_buffered_rows_become_row_groups(tmp_path):
    # Note what this also demonstrates: a Parquet file is only readable once its
    # footer is written on close. That is precisely why the durable record is the
    # JSONL and Parquet is a derived artefact that can always be rebuilt.
    writer = ParquetPartitionWriter(tmp_path, "tick", row_group_size=5)
    writer.write_rows([tick(i) for i in range(12)])
    writer.close()

    path = tmp_path / "tick" / "date=2026-08-27" / "hour=14" / "part-0000.parquet"
    metadata = pq.ParquetFile(path).metadata
    assert metadata.num_rows == 12
    assert metadata.num_row_groups == 3  # 5 + 5 + 2


def test_close_is_idempotent(tmp_path):
    writer = ParquetPartitionWriter(tmp_path, "tick")
    writer.write_rows([tick(0)])
    writer.close()
    writer.close()
    assert read_table(tmp_path, "tick").height == 1


# ------------------------------------------------------------------ reading


def test_scan_exposes_partitions_as_columns(tmp_path):
    with ParquetPartitionWriter(tmp_path, "tick") as writer:
        writer.write_rows([tick(0)])

    frame = scan_table(tmp_path, "tick").collect()
    assert frame["date"].cast(pl.String).to_list() == ["2026-08-27"]
    assert frame["hour"].cast(pl.String).to_list() == ["14"]


def test_scanning_a_missing_table_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="tick"):
        scan_table(tmp_path, "tick")


def test_validation_passes_on_a_well_formed_table(tmp_path):
    with ParquetPartitionWriter(tmp_path, "tick") as writer:
        writer.write_rows([tick(i) for i in range(5)])

    report = validate_table(tmp_path, "tick")
    assert report.ok, report.errors
    assert report.rows == 5
    assert report.symbols == ["BTC/USD"]
    assert report.schema_versions == [SCHEMA_VERSION]
    assert "OK" in report.describe()


def test_validation_flags_a_row_in_the_wrong_partition(tmp_path):
    # Write a row into the 14:00 partition whose own timestamp says 15:00 — the exact
    # corruption a bug in the partition key would produce.
    directory = tmp_path / "tick" / "date=2026-08-27" / "hour=14"
    directory.mkdir(parents=True)
    pq.write_table(
        rows_to_table("tick", [tick(0, wall_ns=BASE_WALL_NS + HOUR_NS)]),
        directory / "part-0000.parquet",
    )

    report = validate_table(tmp_path, "tick")
    assert not report.ok
    assert any("wrong hour partition" in error for error in report.errors)


def test_validation_flags_a_sequence_that_goes_backwards(tmp_path):
    directory = tmp_path / "tick" / "date=2026-08-27" / "hour=14"
    directory.mkdir(parents=True)
    rows = [tick(0) | {"recv_wall_ns": BASE_WALL_NS + 10}, tick(1) | {"recv_wall_ns": BASE_WALL_NS}]
    pq.write_table(rows_to_table("tick", rows), directory / "part-0000.parquet")

    report = validate_table(tmp_path, "tick")
    assert not report.ok
    assert any("recv_wall_ns decreases" in error for error in report.errors)


def test_validation_rejects_an_unreadable_schema_version(tmp_path):
    directory = tmp_path / "tick" / "date=2026-08-27" / "hour=14"
    directory.mkdir(parents=True)
    table = rows_to_table("tick", [tick(0)])
    bumped = table.set_column(0, "schema_version", [[SCHEMA_VERSION + 99]]).cast(schema_for("tick"))
    pq.write_table(bumped, directory / "part-0000.parquet")

    report = validate_table(tmp_path, "tick")
    assert not report.ok
    assert any("schema version" in error for error in report.errors)


def test_validation_reports_a_missing_table(tmp_path):
    report = validate_table(tmp_path, "signal")
    assert not report.ok
    assert report.files == 0


# --------------------------------------------------------------- conversion


def test_tick_rows_fan_out_one_row_per_level():
    message = make_message(0, snapshot_payload(levels=3))
    frame = parse_frame(message.payload)
    assert isinstance(frame, BookFrame)

    rows = tick_rows(message, frame)
    assert len(rows) == 6
    assert [r["side"] for r in rows] == ["bid"] * 3 + ["ask"] * 3
    assert all(r["is_snapshot"] for r in rows)
    assert all(r["seq"] == 0 for r in rows)


def test_deletions_survive_conversion():
    message = make_message(0, update_payload(64_000.1, 0.0, side="ask"))
    rows = tick_rows(message, parse_frame(message.payload))
    assert [(r["side"], r["qty"]) for r in rows] == [("ask", 0.0)]


def test_snapshot_row_holds_the_whole_image():
    message = make_message(0, snapshot_payload(levels=4))
    row = snapshot_row(message, parse_frame(message.payload))
    assert row["depth"] == 4
    assert len(row["bids"]) == 4
    assert row["source"] == "venue"


def test_signal_row_allows_a_null_value():
    row = signal_row(make_message(0, snapshot_payload()), "BTC/USD", "microprice", None)
    assert row["value"] is None
    assert rows_to_table("signal", [row]).column("value").to_pylist() == [None]


def test_export_writes_both_tables_and_they_validate(tmp_path, recording):
    from l2tca.feed.replay import JsonlReplay

    report = export_to_parquet(JsonlReplay(recording).iter_messages(), tmp_path)

    assert report.messages == 7
    assert report.frame_counts["BookFrame"] == 4
    assert report.frame_counts["HeartbeatFrame"] == 1
    assert report.snapshot_rows == 1
    assert report.tick_rows == 6 + 3  # snapshot levels plus three deltas
    assert validate_table(tmp_path, "tick").ok
    assert validate_table(tmp_path, "snapshot").ok


def test_export_skips_undecodable_records_without_dying(tmp_path, recording):
    from l2tca.feed.replay import JsonlReplay
    from l2tca.feed.types import RawMessage

    good = list(JsonlReplay(recording).iter_messages())
    corrupt = RawMessage(99, 0, 1, BASE_WALL_NS, "{not json")
    report = export_to_parquet([*good, corrupt], tmp_path)

    assert report.messages == len(good) + 1 - 1  # the corrupt one never reaches the counter
    assert validate_table(tmp_path, "tick").ok
