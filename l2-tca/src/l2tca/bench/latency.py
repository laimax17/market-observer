"""Latency sampling and percentile reporting.

Deliberately simple and dependency-free: an ``array('q')`` of nanosecond samples and
nearest-rank percentiles. For a book-update benchmark this is the right trade — the
recorder itself must cost far less than the thing it measures, and a plain array
append is a handful of nanoseconds.

Measurement notes that matter when reading the numbers:

* ``time.perf_counter_ns()`` itself costs on the order of tens of nanoseconds, and
  each sample pays for two calls. Below ~200ns of real work the overhead is a
  visible fraction of the result; compare against
  :class:`~l2tca.bench.harness.NoOpTarget`, which measures exactly that floor.
* The first few hundred iterations are dominated by cold caches and, on PyPy or with
  a JIT, by warm-up. Use ``warmup`` in the harness rather than reading p50 off a
  cold run.
* CPython's cyclic GC can add a millisecond-scale outlier that has nothing to do
  with the code under test. The harness can disable it for the measured window; the
  honest reading is to report both.
* Percentiles are of *per-message service time*, not of end-to-end latency under
  load. They say nothing about queueing — that is what ``since_recv`` measures on a
  live feed.
"""

from __future__ import annotations

import gc
import math
import time
from array import array
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

#: Percentiles reported by default.
DEFAULT_QUANTILES = (0.5, 0.9, 0.99, 0.999)


@dataclass(frozen=True, slots=True)
class LatencyStats:
    """Summary of one set of latency samples, all values in nanoseconds."""

    label: str
    count: int
    dropped: int
    min_ns: int
    mean_ns: float
    max_ns: int
    quantiles: dict[float, int]

    def get(self, quantile: float) -> int:
        return self.quantiles[quantile]

    def describe(self) -> str:
        if not self.count:
            return f"{self.label}: no samples"
        parts = [f"n={self.count}"]
        if self.dropped:
            parts.append(f"dropped={self.dropped}")
        parts.append(f"min={self.min_ns / 1000:.2f}us")
        parts.append(f"mean={self.mean_ns / 1000:.2f}us")
        parts.extend(
            f"p{q * 100:g}={value / 1000:.2f}us" for q, value in sorted(self.quantiles.items())
        )
        parts.append(f"max={self.max_ns / 1000:.2f}us")
        return f"{self.label}: " + "  ".join(parts)


def percentile(sorted_samples: list[int] | array, quantile: float) -> int:
    """Nearest-rank percentile of an already-sorted sample list.

    Nearest-rank (rather than interpolation) is the convention in latency work: the
    reported p99 is a value that actually occurred, which is what you want when the
    number ends up in an SLA.
    """
    if not 0.0 < quantile <= 1.0:
        raise ValueError("quantile must be in (0, 1]")
    n = len(sorted_samples)
    if n == 0:
        raise ValueError("no samples")
    rank = max(1, math.ceil(quantile * n))
    return int(sorted_samples[rank - 1])


class LatencyRecorder:
    """Collect nanosecond durations and summarise them.

    ``capacity`` bounds memory; once it is reached, further samples are counted in
    :attr:`dropped` rather than stored, so a runaway benchmark degrades into a
    truncated sample instead of an OOM.
    """

    __slots__ = ("_dropped", "_samples", "capacity", "label")

    def __init__(self, label: str = "latency", capacity: int = 2_000_000) -> None:
        self.label = label
        self.capacity = capacity
        self._samples: array = array("q")
        self._dropped = 0

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def samples(self) -> array:
        """The raw samples, in recording order (useful for plotting a time series)."""
        return self._samples

    def record(self, duration_ns: int) -> None:
        if len(self._samples) < self.capacity:
            self._samples.append(duration_ns)
        else:
            self._dropped += 1

    @contextmanager
    def measure(self) -> Iterator[None]:
        """Time a block. Convenient, but adds a generator round-trip per sample.

        For the hot loop in :mod:`l2tca.bench.harness` the timing is inlined instead.
        """
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            self.record(time.perf_counter_ns() - start)

    def reset(self) -> None:
        self._samples = array("q")
        self._dropped = 0

    def stats(self, quantiles: tuple[float, ...] = DEFAULT_QUANTILES) -> LatencyStats:
        if not self._samples:
            return LatencyStats(self.label, 0, self._dropped, 0, 0.0, 0, {})
        ordered = sorted(self._samples)
        return LatencyStats(
            label=self.label,
            count=len(ordered),
            dropped=self._dropped,
            min_ns=ordered[0],
            mean_ns=sum(ordered) / len(ordered),
            max_ns=ordered[-1],
            quantiles={q: percentile(ordered, q) for q in quantiles},
        )


@contextmanager
def gc_paused(enabled: bool = True) -> Iterator[None]:
    """Disable cyclic GC for the measured window, restoring the previous state after.

    A collection during a benchmark shows up as a fat tail that is real in
    production but is not a property of the code under test. Measure both ways.
    """
    if not enabled or not gc.isenabled():
        yield
        return
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def time_call(fn: Callable[[], object]) -> int:
    """Nanoseconds spent in one call — used by tests and one-off measurements."""
    start = time.perf_counter_ns()
    fn()
    return time.perf_counter_ns() - start
