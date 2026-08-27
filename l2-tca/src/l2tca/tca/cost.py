"""Execution cost analysis — SPECIFIED, INTENTIONALLY UNIMPLEMENTED.

The question this layer answers: *what would this order have cost, against the book
that was actually resting at the time?* Everything needed to answer it — a
timestamped book replica and a deterministic replay — is already in place; what is
missing is the accounting, which is the part worth writing by hand.

Sign convention (used everywhere below)
---------------------------------------
Costs are **positive when they hurt**. A buy filled above its reference price has
positive slippage; so does a sell filled below. Getting this backwards is the
classic TCA bug: a wrong sign turns a cost into an apparent alpha, and it survives
review because the magnitudes look plausible.

Reference prices, and when each is the right one
------------------------------------------------
``arrival``
    Mid at the moment the order was released. The standard benchmark for
    implementation shortfall; captures everything that happened after the decision.
``mid at fill``
    Mid immediately before the fill. Isolates the cost of *crossing* from the cost
    of *waiting*.
``VWAP over the order's life``
    Compares against the market rather than against a point in time; forgiving of a
    slow order in a trending market, which is either a feature or a way to hide
    drift, depending on who is asking.

A note on what an L2 book can and cannot tell you
--------------------------------------------------
Walking the resting book gives the **mechanical** cost of consuming visible
liquidity. It does not model market impact: it assumes the book does not move while
being consumed, no hidden or iceberg liquidity refills, and no one reacts to the
order. Real cost for anything beyond a small clip is higher. Being explicit about
this gap is part of a defensible answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from l2tca.book import OrderBook
from l2tca.feed.parser import Level

#: ``"buy"`` consumes asks, ``"sell"`` consumes bids.
Side = str


@dataclass(frozen=True, slots=True)
class Fill:
    """One price level consumed by a simulated order."""

    price: float
    qty: float

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass(frozen=True, slots=True)
class SweepResult:
    """The outcome of walking a marketable order through the resting book."""

    side: Side
    requested_qty: float
    filled_qty: float
    fills: tuple[Fill, ...] = field(default_factory=tuple)
    reference_price: float | None = None

    @property
    def unfilled_qty(self) -> float:
        """Size the visible book could not fill — the book ran out of depth."""
        return max(0.0, self.requested_qty - self.filled_qty)

    @property
    def notional(self) -> float:
        return sum(fill.notional for fill in self.fills)

    @property
    def vwap(self) -> float | None:
        """Volume-weighted average fill price, or ``None`` if nothing filled."""
        if self.filled_qty <= 0:
            return None
        return self.notional / self.filled_qty

    @property
    def levels_consumed(self) -> int:
        return len(self.fills)


def sweep(
    book: OrderBook, side: Side, qty: float, *, limit_price: float | None = None
) -> SweepResult:
    """Walk ``qty`` through the resting book and report the fills.

    Rules:

    * A buy consumes asks from the best price upwards; a sell consumes bids downwards.
    * Partially consume the final level — do not round up to whole levels.
    * With ``limit_price``, stop at the first level worse than it and leave the
      remainder unfilled (that residual is a real cost: it is the part that has to
      be worked, or missed).
    * If the visible book runs out first, return with
      :attr:`SweepResult.unfilled_qty` set rather than raising: "the book was too
      thin" is an answer, not an error.
    * ``reference_price`` should be the mid at the time of the sweep, so downstream
      cost functions do not have to re-derive it from a book that has since moved.
    """
    raise NotImplementedError("tca.sweep — see the module docstring")


def slippage_bps(result: SweepResult, reference_price: float | None = None) -> float | None:
    """Cost of the sweep against a reference price, in basis points, positive = worse.

    ``(vwap - reference) / reference * 10_000`` for a buy, negated for a sell.
    Returns ``None`` if nothing filled or no reference price is available.
    """
    raise NotImplementedError("tca.slippage_bps")


def effective_spread_bps(result: SweepResult, mid: float) -> float | None:
    """``2 * |vwap - mid| / mid * 10_000``.

    Doubled by convention so it is comparable with the quoted spread: crossing half
    the spread on each side of a round trip costs the full spread.
    """
    raise NotImplementedError("tca.effective_spread_bps")


def implementation_shortfall_bps(
    arrival_mid: float,
    result: SweepResult,
    *,
    fees_bps: float = 0.0,
) -> float | None:
    """Total shortfall against the arrival mid, in bps, positive = worse.

    Decomposes into three parts worth reporting separately rather than as one
    number, because they have different owners:

    * **Spread / impact cost** — ``vwap`` versus the mid at execution time (the
      trader's cost of crossing).
    * **Delay cost** — mid at execution versus arrival mid (the cost of waiting,
      which belongs to the scheduler, not the crossing decision).
    * **Opportunity cost** — the unfilled residual valued at the arrival mid.

    ``fees_bps`` is added on top: a taker fee is part of the cost of crossing and
    leaving it out flatters every aggressive strategy.
    """
    raise NotImplementedError("tca.implementation_shortfall_bps")


def cost_curve(book: OrderBook, side: Side, sizes: list[float]) -> list[tuple[float, float | None]]:
    """``[(size, slippage_bps), ...]`` — the book's liquidity profile at one instant.

    The shape is the useful output: a nearly flat curve means the book absorbs the
    clip, and the elbow marks where an order should start being worked instead of
    crossed. Computing it as one walk that records the running VWAP is O(levels),
    versus O(len(sizes) * levels) for repeated independent sweeps.
    """
    raise NotImplementedError("tca.cost_curve")


def participation_schedule(total_qty: float, buckets: int, curve: list[float]) -> list[float]:
    """Split ``total_qty`` across ``buckets`` in proportion to expected volume.

    The scheduling side of TCA: given a normalised intraday volume profile, a
    participation-rate schedule trades more when the market does. Included as the
    natural next step after measuring cost — measuring it is only useful if
    something changes as a result.
    """
    raise NotImplementedError("tca.participation_schedule")


def levels_for_side(book: OrderBook, side: Side, n: int) -> list[Level]:
    """The ``n`` best levels a ``side`` order would consume (asks for a buy)."""
    raise NotImplementedError("tca.levels_for_side")
