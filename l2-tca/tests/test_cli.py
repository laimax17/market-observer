"""The CLI is the interface the project is actually driven through; smoke it end to end."""

from __future__ import annotations

import pytest

from l2tca.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert "l2tca" in capsys.readouterr().out


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_replay_summarises_a_recording(recording, capsys):
    assert main(["--log-level", "WARNING", "replay", str(recording), "--speed", "0"]) == 0
    out = capsys.readouterr().out
    assert "replayed 7 messages" in out
    assert "BookFrame (snapshot)" in out
    assert "HeartbeatFrame" in out


def test_replay_can_print_decoded_frames(recording, capsys):
    main(["--log-level", "WARNING", "replay", str(recording), "--speed", "0", "--show", "2"])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("seq=")]
    assert len(lines) == 2


def test_export_then_inspect(recording, tmp_path, capsys):
    out = tmp_path / "parquet"
    assert main(["--log-level", "WARNING", "export", str(recording), "--out", str(out)]) == 0
    assert "tick_rows=9" in capsys.readouterr().out

    assert main(["--log-level", "WARNING", "inspect", "--root", str(out), "--table", "tick"]) == 0
    report = capsys.readouterr().out
    assert "status         OK" in report
    assert "BTC/USD" in report


def test_inspect_fails_loudly_when_there_is_nothing_to_inspect(tmp_path, capsys):
    # An empty archive is a failure, not a quiet success: this is the check a
    # capture job runs afterwards to prove it produced something.
    assert main(["--log-level", "WARNING", "inspect", "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert out.count("status         FAILED") == 3
    assert "no parquet files under" in out


def test_bench_runs_the_suite(recording, capsys):
    assert main(["--log-level", "WARNING", "bench", str(recording), "--warmup", "0"]) == 0
    out = capsys.readouterr().out
    assert "parse-only" in out and "p99" in out
    assert "book: SKIPPED" in out


def test_bench_on_an_empty_recording(tmp_path, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert main(["--log-level", "WARNING", "bench", str(empty)]) == 1
    assert "no messages" in capsys.readouterr().out


def test_missing_file_is_a_clean_error(tmp_path, capsys):
    assert main(["--log-level", "WARNING", "replay", str(tmp_path / "nope.jsonl")]) == 2
    assert "error:" in capsys.readouterr().err


def test_record_against_the_mock_venue(tmp_path, capsys):
    exit_code = main(
        [
            "--log-level",
            "WARNING",
            "record",
            "--mock",
            "--mock-rate",
            "500",
            "--max-messages",
            "40",
            "--depth",
            "10",
            "--out",
            str(tmp_path / "raw"),
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "recorded 40 messages" in out

    files = list((tmp_path / "raw").glob("mock_btcusd_*.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text().splitlines()) == 40


def test_recorded_output_flows_straight_into_export(tmp_path, capsys):
    main(
        [
            "--log-level",
            "WARNING",
            "record",
            "--mock",
            "--mock-rate",
            "500",
            "--max-messages",
            "60",
            "--depth",
            "10",
            "--out",
            str(tmp_path / "raw"),
        ]
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--log-level",
                "WARNING",
                "export",
                str(tmp_path / "raw"),
                "--out",
                str(tmp_path / "parquet"),
            ]
        )
        == 0
    )
    assert "status         OK" in capsys.readouterr().out


def test_an_invalid_depth_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        main(["record", "--depth", "7"])
