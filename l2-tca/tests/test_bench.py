"""The measurement harness has to be trustworthy before its numbers mean anything."""

from __future__ import annotations

import pytest

from l2tca.bench.harness import (
    BookTarget,
    DecodeTarget,
    NoOpTarget,
    ParseOnlyTarget,
    run_benchmark,
    run_suite,
)
from l2tca.bench.latency import LatencyRecorder, gc_paused, percentile, time_call

# ---------------------------------------------------------------- percentiles


@pytest.mark.parametrize(
    ("quantile", "expected"),
    [(0.5, 50), (0.9, 90), (0.99, 99), (1.0, 100)],
)
def test_nearest_rank_percentiles(quantile, expected):
    assert percentile(list(range(1, 101)), quantile) == expected


def test_percentiles_return_a_value_that_actually_occurred():
    samples = sorted([1, 1, 1, 1_000_000])
    assert percentile(samples, 0.99) in samples


@pytest.mark.parametrize("quantile", [0.0, -0.1, 1.5])
def test_invalid_quantiles_are_rejected(quantile):
    with pytest.raises(ValueError, match="quantile"):
        percentile([1, 2, 3], quantile)


def test_percentile_of_nothing_is_an_error():
    with pytest.raises(ValueError, match="no samples"):
        percentile([], 0.5)


# ------------------------------------------------------------------ recorder


def test_recorder_summarises_samples():
    recorder = LatencyRecorder("unit")
    for value in range(1, 101):
        recorder.record(value)

    stats = recorder.stats()
    assert stats.count == 100
    assert stats.min_ns == 1
    assert stats.max_ns == 100
    assert stats.mean_ns == pytest.approx(50.5)
    assert stats.get(0.5) == 50
    assert stats.get(0.99) == 99
    assert "p50" in stats.describe()


def test_recorder_caps_memory_and_counts_what_it_dropped():
    recorder = LatencyRecorder("unit", capacity=10)
    for value in range(100):
        recorder.record(value)

    assert len(recorder) == 10
    assert recorder.dropped == 90
    assert recorder.stats().count == 10


def test_empty_recorder_does_not_explode():
    stats = LatencyRecorder("unit").stats()
    assert stats.count == 0
    assert "no samples" in stats.describe()


def test_measure_context_records_a_positive_duration():
    recorder = LatencyRecorder("unit")
    with recorder.measure():
        sum(range(1000))
    assert len(recorder) == 1
    assert recorder.samples[0] > 0


def test_reset_clears_samples():
    recorder = LatencyRecorder("unit")
    recorder.record(5)
    recorder.reset()
    assert len(recorder) == 0


def test_gc_paused_restores_the_previous_state():
    import gc

    assert gc.isenabled()
    with gc_paused(True):
        assert not gc.isenabled()
    assert gc.isenabled()

    with gc_paused(False):
        assert gc.isenabled()


def test_time_call_measures_something():
    assert time_call(lambda: sum(range(10_000))) > 0


# ------------------------------------------------------------------- harness


def test_warmup_messages_are_applied_but_not_measured(messages):
    class Counting:
        def __init__(self) -> None:
            self.calls = 0

        def apply(self, message) -> None:
            self.calls += 1

    target = Counting()
    result = run_benchmark(messages, target, warmup=2, label="counting")

    assert target.calls == len(messages)  # warmup messages are still applied
    assert result.messages == len(messages) - 2  # but not counted
    assert result.apply.count == len(messages) - 2
    assert result.warmup == 2


def test_warmup_larger_than_the_input_leaves_nothing_to_measure(messages):
    result = run_benchmark(messages, NoOpTarget(), warmup=1_000)
    assert result.messages == 0
    assert result.apply.count == 0


def test_the_harness_floor_is_cheaper_than_json_parsing(messages):
    corpus = messages * 200
    noop = run_benchmark(corpus, NoOpTarget(), warmup=100)
    parse = run_benchmark(corpus, ParseOnlyTarget(), warmup=100)
    decode = run_benchmark(corpus, DecodeTarget(), warmup=100)

    assert noop.apply.get(0.5) < parse.apply.get(0.5) < decode.apply.get(0.5)
    assert noop.throughput_msg_s > parse.throughput_msg_s
    assert "msg/s" in noop.describe()


def test_generators_are_materialised_before_timing(messages):
    result = run_benchmark((m for m in messages), NoOpTarget(), warmup=0)
    assert result.messages == len(messages)


def test_an_unimplemented_book_is_reported_not_raised(messages):
    result = run_benchmark(messages, BookTarget(), warmup=0, label="book")
    assert result.error is not None
    assert "not implemented" in result.error
    assert "SKIPPED" in result.describe()


def test_the_default_suite_runs_every_target(messages):
    suite = run_suite(messages * 20, warmup=10)
    labels = [result.label for result in suite.results]
    assert labels == ["noop", "parse-only", "decode", "book"]
    # Three reference targets work; the book is a specified stub for now.
    assert [r.error is None for r in suite.results] == [True, True, True, False]
    assert "book: SKIPPED" in suite.describe()
