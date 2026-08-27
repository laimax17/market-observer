"""The benchmark harness: wrap a per-message call and report its latency profile.

The measured unit is one call to ``target.apply(message)`` — for the real target
that is "apply one venue frame to the order book". The harness feeds it recorded
messages, so two runs over the same file execute exactly the same work and the
numbers are comparable across a refactor.

Reference targets are included so a book implementation can be read against a floor
rather than in isolation:

``NoOpTarget``
    Measures the harness itself: two clock reads and a method call.
``ParseOnlyTarget``
    ``json.loads`` and nothing else — the unavoidable cost of touching the frame.
``DecodeTarget``
    Full typed decode into :class:`~l2tca.feed.parser.Frame` objects.
``BookTarget``
    The real thing: decode, then apply to :class:`~l2tca.book.OrderBook`.

If the book is still a specified stub, ``BookTarget`` reports that cleanly instead
of failing the run — the harness is meant to be usable from day one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from l2tca.bench.latency import DEFAULT_QUANTILES, LatencyRecorder, LatencyStats, gc_paused
from l2tca.feed.parser import BookFrame, parse_frame
from l2tca.feed.types import RawMessage


@runtime_checkable
class UpdateTarget(Protocol):
    """Anything the harness can time once per message."""

    def apply(self, message: RawMessage) -> None: ...


class NoOpTarget:
    """Does nothing: the measurement floor of the harness itself."""

    def apply(self, message: RawMessage) -> None:
        pass


class ParseOnlyTarget:
    """JSON decode only — the cost every downstream design has to pay."""

    def apply(self, message: RawMessage) -> None:
        json.loads(message.payload)


class DecodeTarget:
    """Full typed decode, including price-level objects."""

    def apply(self, message: RawMessage) -> None:
        parse_frame(message.payload)


class BookTarget:
    """Decode and apply to an order book implementation.

    ``book`` must expose ``apply_snapshot(frame)`` and ``apply_update(frame)`` as
    specified in :mod:`l2tca.book.orderbook`.
    """

    def __init__(self, book: object | None = None) -> None:
        if book is None:
            from l2tca.book import OrderBook

            book = OrderBook()
        self.book = book

    def apply(self, message: RawMessage) -> None:
        frame = parse_frame(message.payload)
        if not isinstance(frame, BookFrame):
            return
        if frame.is_snapshot:
            self.book.apply_snapshot(frame)  # type: ignore[attr-defined]
        else:
            self.book.apply_update(frame)  # type: ignore[attr-defined]


@dataclass(slots=True)
class BenchmarkResult:
    """One target's run over one message set."""

    label: str
    messages: int
    warmup: int
    elapsed_s: float
    apply: LatencyStats
    since_recv: LatencyStats | None = None
    gc_disabled: bool = True
    error: str | None = None

    @property
    def throughput_msg_s(self) -> float:
        return self.messages / self.elapsed_s if self.elapsed_s > 0 else float("nan")

    def describe(self) -> str:
        if self.error:
            return f"{self.label}: SKIPPED — {self.error}"
        lines = [
            f"{self.label}: {self.messages} msgs in {self.elapsed_s * 1e3:.1f}ms "
            f"({self.throughput_msg_s:,.0f} msg/s, warmup={self.warmup}, "
            f"gc={'off' if self.gc_disabled else 'on'})",
            "  " + self.apply.describe(),
        ]
        if self.since_recv is not None:
            lines.append("  " + self.since_recv.describe())
        return "\n".join(lines)


@dataclass(slots=True)
class BenchmarkSuite:
    """A set of results printed together, so targets can be read against each other."""

    results: list[BenchmarkResult] = field(default_factory=list)

    def describe(self) -> str:
        return "\n".join(result.describe() for result in self.results)


def run_benchmark(
    messages: Sequence[RawMessage] | Iterable[RawMessage],
    target: UpdateTarget,
    *,
    label: str | None = None,
    warmup: int = 1_000,
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
    gc_disabled: bool = True,
    measure_since_recv: bool = False,
) -> BenchmarkResult:
    """Time ``target.apply`` over ``messages`` and summarise.

    The first ``warmup`` messages are applied untimed and then excluded from the
    measured set, so warm-up work (imports, first-touch allocation, building initial
    book state from the snapshot) does not pollute the percentiles and no message is
    applied twice.

    ``measure_since_recv`` additionally records ``now - message.recv_ns``, which is
    only meaningful on a live feed: ``recv_ns`` comes from the recording process's
    monotonic clock, so on replay the value is nonsense and the flag should stay off.
    """
    materialised = list(messages)  # never time file or generator work
    label = label or type(target).__name__
    warmup = min(warmup, len(materialised))
    measured = materialised[warmup:]

    recorder = LatencyRecorder(f"{label}.apply")
    since_recv = LatencyRecorder(f"{label}.since_recv") if measure_since_recv else None

    apply = target.apply
    perf = time.perf_counter_ns

    try:
        for message in materialised[:warmup]:
            apply(message)
        with gc_paused(gc_disabled):
            loop_start = perf()
            for message in measured:
                start = perf()
                apply(message)
                end = perf()
                recorder.record(end - start)
                if since_recv is not None:
                    since_recv.record(end - message.recv_ns)
            elapsed_ns = perf() - loop_start
    except NotImplementedError as exc:
        return BenchmarkResult(
            label=label,
            messages=0,
            warmup=warmup,
            elapsed_s=0.0,
            apply=recorder.stats(quantiles),
            gc_disabled=gc_disabled,
            error=f"not implemented yet ({exc or 'see the module docstring for the spec'})",
        )

    return BenchmarkResult(
        label=label,
        messages=len(measured),
        warmup=warmup,
        elapsed_s=elapsed_ns / 1e9,
        apply=recorder.stats(quantiles),
        since_recv=since_recv.stats(quantiles) if since_recv is not None else None,
        gc_disabled=gc_disabled,
    )


#: The comparison run by ``l2tca bench``: each layer's cost, plus the book on top.
DEFAULT_TARGETS: dict[str, Callable[[], UpdateTarget]] = {
    "noop": NoOpTarget,
    "parse-only": ParseOnlyTarget,
    "decode": DecodeTarget,
    "book": BookTarget,
}


def run_suite(
    messages: Iterable[RawMessage],
    targets: dict[str, Callable[[], UpdateTarget]] | None = None,
    **kwargs: object,
) -> BenchmarkSuite:
    """Run every target over the same materialised message set."""
    materialised = list(messages)
    suite = BenchmarkSuite()
    for label, factory in (targets or DEFAULT_TARGETS).items():
        try:
            target = factory()
        except NotImplementedError as exc:
            suite.results.append(
                BenchmarkResult(
                    label=label,
                    messages=0,
                    warmup=0,
                    elapsed_s=0.0,
                    apply=LatencyRecorder(label).stats(),
                    error=f"not implemented yet ({exc})",
                )
            )
            continue
        suite.results.append(run_benchmark(materialised, target, label=label, **kwargs))  # type: ignore[arg-type]
    return suite
