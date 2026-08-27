"""Reconnect pacing."""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(slots=True)
class ExponentialBackoff:
    """Exponential backoff with full jitter.

    Full jitter (``uniform(0, cap)`` rather than ``cap`` exactly) matters here: when a
    venue drops every client at once, undelayed clients reconnect in lockstep and get
    dropped again. Randomising the whole interval spreads the herd.

    The deterministic ``rng`` seam exists so tests can assert exact delays.
    """

    initial_s: float = 0.5
    max_s: float = 30.0
    factor: float = 2.0
    rng: random.Random = field(default_factory=random.Random)
    _attempt: int = field(default=0, init=False)

    @property
    def attempt(self) -> int:
        """Number of delays handed out since the last :meth:`reset`."""
        return self._attempt

    def next_delay(self) -> float:
        """Return the next sleep in seconds and advance the attempt counter."""
        ceiling = min(self.max_s, self.initial_s * (self.factor**self._attempt))
        self._attempt += 1
        return self.rng.uniform(0.0, ceiling)

    def reset(self) -> None:
        """Forget the attempt history — call after a connection proves healthy."""
        self._attempt = 0
