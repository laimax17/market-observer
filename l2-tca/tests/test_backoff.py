"""Backoff must grow, stay capped, jitter, and reset."""

from __future__ import annotations

import random

import pytest

from l2tca.feed.backoff import ExponentialBackoff


def _no_jitter() -> random.Random:
    class Max(random.Random):
        def uniform(self, a: float, b: float) -> float:
            return b

    return Max()


def test_delays_double_up_to_the_cap():
    backoff = ExponentialBackoff(initial_s=0.5, max_s=4.0, rng=_no_jitter())
    assert [backoff.next_delay() for _ in range(6)] == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


def test_jitter_stays_inside_the_ceiling():
    backoff = ExponentialBackoff(initial_s=1.0, max_s=8.0, rng=random.Random(0))
    for attempt in range(6):
        ceiling = min(8.0, 1.0 * 2**attempt)
        assert 0.0 <= backoff.next_delay() <= ceiling


def test_reset_starts_the_ladder_again():
    backoff = ExponentialBackoff(initial_s=0.5, max_s=100.0, rng=_no_jitter())
    for _ in range(5):
        backoff.next_delay()
    assert backoff.attempt == 5
    backoff.reset()
    assert backoff.attempt == 0
    assert backoff.next_delay() == pytest.approx(0.5)
