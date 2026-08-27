"""Microstructure factors — SPECIFIED, INTENTIONALLY UNIMPLEMENTED.

Each factor is a pure function of book state (plus, where noted, a short history).
Keeping them pure is what lets the same code run on a live book and on a replayed
one, and lets each be unit-tested against a hand-built book.

Every function takes an :class:`~l2tca.book.OrderBook` and returns ``float`` or
``None``. ``None`` means *undefined for this state* — an empty side, a zero
denominator — and must never be silently replaced with ``0.0``: a zero imbalance and
an unknown imbalance are different facts, and the storage layer records the
difference (``value`` is nullable in the ``signal`` table).

Conventions used throughout:

* ``P_b``, ``Q_b`` — best bid price and size; ``P_a``, ``Q_a`` — best ask.
* "Top-N" means the N best levels on a side, best first.
* Prices are absolute; sizes are in base currency (BTC for BTC/USD).
"""

from __future__ import annotations

from l2tca.book import OrderBook


def mid_price(book: OrderBook) -> float | None:
    """``(P_b + P_a) / 2``.

    The baseline reference price. Its weakness is exactly what the next two factors
    address: it ignores size, so it does not move when one side of the book is
    swept of everything but a token order.
    """
    raise NotImplementedError("signals.mid_price")


def microprice(book: OrderBook) -> float | None:
    """Size-weighted mid: ``(P_b * Q_a + P_a * Q_b) / (Q_a + Q_b)``.

    Note the crossed weighting — the *ask* size weights the *bid* price. The
    intuition: a large resting ask and a thin bid means the next trade is more likely
    to happen at the bid, so fair value sits closer to the bid. Empirically a better
    short-horizon predictor of the next mid than the mid itself.

    Returns ``None`` if either side is empty or the sizes sum to zero.
    """
    raise NotImplementedError("signals.microprice")


def order_book_imbalance(book: OrderBook, levels: int = 1) -> float | None:
    """``(Qb - Qa) / (Qb + Qa)`` summed over the top ``levels`` per side.

    Range ``[-1, +1]``: positive means bid-heavy. With ``levels=1`` this is
    top-of-book imbalance, the classic short-horizon direction signal; deeper
    aggregations are steadier but slower to react.

    Returns ``None`` when the combined size is zero.
    """
    raise NotImplementedError("signals.order_book_imbalance")


def spread_bps(book: OrderBook) -> float | None:
    """Quoted spread in basis points: ``(P_a - P_b) / mid * 10_000``.

    Basis points rather than absolute price so the number stays comparable across
    instruments and across a large price move in one instrument.
    """
    raise NotImplementedError("signals.spread_bps")


def depth_imbalance(book: OrderBook, depth_bps: float = 10.0) -> float | None:
    """Imbalance of resting size within ``depth_bps`` of the mid.

    Unlike :func:`order_book_imbalance`, the window is defined by *price distance*
    rather than level count, so it is not distorted by one side quoting in finer
    increments than the other. Build it on
    :meth:`~l2tca.book.OrderBook.cumulative_qty`.
    """
    raise NotImplementedError("signals.depth_imbalance")


def book_slope(book: OrderBook, levels: int = 10) -> float | None:
    """Size accumulated per unit of price distance from the mid, averaged over both sides.

    One workable definition: regress cumulative size against ``|price - mid|`` over
    the top ``levels`` and return the slope. A steep slope means a resilient book —
    a marketable order walks few ticks. This is the book-shape counterpart to the
    TCA layer's realised sweep cost, and comparing the two is where the interesting
    result lives.
    """
    raise NotImplementedError("signals.book_slope")


def queue_imbalance_at(book: OrderBook, price: float) -> float | None:
    """Share of resting size at ``price`` relative to the total at that level's side.

    Useful as a crude queue-position proxy when the venue does not publish per-order
    detail, which Kraken's L2 feed does not.
    """
    raise NotImplementedError("signals.queue_imbalance_at")


#: Factor name -> callable, used when writing the ``signal`` table. Names are stored
#: as data, so adding a factor never requires a schema change.
FACTORS = {
    "mid_price": mid_price,
    "microprice": microprice,
    "obi_1": lambda book: order_book_imbalance(book, levels=1),
    "obi_5": lambda book: order_book_imbalance(book, levels=5),
    "spread_bps": spread_bps,
    "depth_imbalance_10bps": lambda book: depth_imbalance(book, depth_bps=10.0),
    "book_slope_10": lambda book: book_slope(book, levels=10),
}
