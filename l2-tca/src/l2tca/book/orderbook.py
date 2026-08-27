"""Order book reconstruction — SPECIFIED, INTENTIONALLY UNIMPLEMENTED.

This is the core of the project and is written by hand, on purpose. Everything
around it (feed, recording, replay, storage, benchmarks, tests) is finished, so an
implementation can be developed entirely offline against a recorded file and
measured the moment it runs.

What "correct" means here
-------------------------
Kraken's v2 ``book`` channel publishes a **fixed-depth** book: one snapshot of the
top ``depth`` levels per side, then deltas. The local replica is correct when, after
every applied frame:

1. Bid prices are strictly descending, ask prices strictly ascending.
2. Every stored level has ``qty > 0``.
3. The book is not crossed: ``best_bid < best_ask``.
4. Neither side holds more than ``depth`` levels.
5. The venue's checksum over the local state matches the one on the frame.

Delta semantics
---------------
* ``qty`` in an update is the **new absolute resting size** at that price, never a
  increment. Apply it as a replace.
* ``qty == 0`` is a **deletion** of that price level.
* A price not currently held is an **insertion**.
* Because the book is fixed-depth, an insertion inside the top ``depth`` pushes the
  far level off the end: it must be dropped locally, even though the venue never
  sends a delete for it. This is the single most common source of a slowly drifting
  replica, and the checksum is what catches it.
* A snapshot **replaces** all state for that side. After a reconnect the venue sends
  a fresh snapshot; any state carried across the gap is stale and must be discarded
  (:attr:`RawMessage.session` marks the boundary).

Kraken v2 checksum
------------------
The venue publishes a CRC32 over the top of the book so a replica can detect drift.
The algorithm:

1. Take the top 10 **asks** (ascending), then the top 10 **bids** (descending).
2. Format each level's price and quantity as a fixed-point decimal string using that
   pair's ``price`` / ``qty`` precision from the REST ``/0/public/AssetPairs``
   endpoint (e.g. price precision 1 and qty precision 8 for BTC/USD).
3. Remove the decimal point, then strip leading zeros.
4. Concatenate price and qty for each level, asks first then bids, into one string.
5. ``zlib.crc32`` of that string's ASCII bytes, as an unsigned 32-bit integer.

Two practical consequences worth being able to explain:

* The checksum is defined over the **decimal string the venue sent**, so a replica
  that stores ``float`` prices has to re-format them at exactly the right precision.
  Storing prices as scaled integers (``round(price * 10**precision)``) sidesteps a
  whole class of formatting and equality bugs — which is also why the recorder keeps
  the untouched payload: the original digits are always recoverable.
* A checksum mismatch is not repairable locally. The only correct response is to
  resubscribe and rebuild from a fresh snapshot, and to count the event — a rising
  mismatch rate is a real signal about the feed handler, not noise.

Implementation choices to weigh (and be ready to defend)
--------------------------------------------------------
* ``dict[int, float]`` keyed by scaled integer price, plus a sorted key list
  maintained with :mod:`bisect`: O(1) lookup, O(depth) insert/delete from the list
  shift. At depth 100 the memmove is tiny and this is usually the fastest option in
  CPython.
* ``sortedcontainers.SortedDict``: O(log n) operations, clean code, an extra
  dependency and more Python-level indirection per update.
* Two fixed-size arrays with in-place shifting: best cache behaviour, most code.
* Heaps: attractive until you need the *k*-th level or a delete-by-price, both of
  which this book needs on every frame.

Performance target: the harness in :mod:`l2tca.bench` reports p50/p99 for one
``apply``. On recorded BTC/USD depth-100 data a reasonable CPython target is p50
under ~10us and p99 under ~50us per delta frame, JSON decode included; compare
against ``parse-only`` to see how much of that is the book at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from l2tca.feed.parser import BookFrame, Level


class BookError(Exception):
    """Base class for order book faults."""


class ChecksumMismatch(BookError):
    """Local state diverged from the venue's. Resubscribe and rebuild."""


class SequenceError(BookError):
    """A frame arrived that cannot be applied to the current state.

    Examples: a delta before any snapshot, or a frame for a different symbol.
    """


@dataclass(frozen=True, slots=True)
class TopOfBook:
    """Best bid and ask at a point in time. ``None`` where a side is empty."""

    bid_price: float | None
    bid_qty: float | None
    ask_price: float | None
    ask_qty: float | None

    @property
    def mid(self) -> float | None:
        if self.bid_price is None or self.ask_price is None:
            return None
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread(self) -> float | None:
        if self.bid_price is None or self.ask_price is None:
            return None
        return self.ask_price - self.bid_price


class OrderBook:
    """A single-symbol, fixed-depth L2 book replica.

    The constructor holds configuration only; choosing the state representation is
    part of the exercise (see the module docstring for the options and their
    trade-offs).

    Attributes to maintain while implementing:

    ``self.symbol`` / ``self.depth``
        Configuration, set here.
    ``self.checksum_failures``
        Count of mismatches — a health metric worth exporting, not just an error.
    ``self.last_seq`` / ``self.last_session``
        Provenance of the most recently applied frame, so a caller can tell whether
        the book is current and which session it belongs to.
    """

    def __init__(self, symbol: str = "BTC/USD", depth: int = 100, *, verify_checksum: bool = False):
        self.symbol = symbol
        self.depth = depth
        self.verify_checksum = verify_checksum
        self.checksum_failures = 0
        self.last_seq: int | None = None
        self.last_session: int | None = None
        # TODO(implementer): the bid/ask state lives here.

    # ------------------------------------------------------------- mutation

    def apply_snapshot(self, frame: BookFrame) -> None:
        """Replace all state with the venue's image.

        Must be total: any level held from before this call is gone afterwards,
        including on a reconnect where the previous state looked healthy.

        Raises :class:`SequenceError` if ``frame.symbol`` is not this book's symbol.
        """
        raise NotImplementedError("OrderBook.apply_snapshot — see the module docstring")

    def apply_update(self, frame: BookFrame) -> None:
        """Apply one delta frame.

        Contract:

        * ``qty > 0`` at a known price replaces that level's size.
        * ``qty > 0`` at an unknown price inserts a level, then trims the far end of
          that side back to ``depth``.
        * ``qty == 0`` deletes the level; deleting a price that is not held is not an
          error (it may have already fallen off the local fixed-depth window).
        * Raises :class:`SequenceError` if no snapshot has been applied yet.
        * When ``verify_checksum`` is set and ``frame.checksum`` is not ``None``,
          compare against :meth:`checksum` *after* applying, increment
          :attr:`checksum_failures` and raise :class:`ChecksumMismatch`.

        This method is the one the benchmark harness times. Keep allocations out of
        it: it runs tens of times a second per symbol in production and is where a
        Python implementation either holds up or does not.
        """
        raise NotImplementedError("OrderBook.apply_update — see the module docstring")

    # ------------------------------------------------------------ inspection

    def best_bid(self) -> Level | None:
        """Highest bid, or ``None`` when the bid side is empty."""
        raise NotImplementedError("OrderBook.best_bid")

    def best_ask(self) -> Level | None:
        """Lowest ask, or ``None`` when the ask side is empty."""
        raise NotImplementedError("OrderBook.best_ask")

    def top_of_book(self) -> TopOfBook:
        """Best bid and ask together — one traversal, not two."""
        raise NotImplementedError("OrderBook.top_of_book")

    def top(self, n: int) -> tuple[list[Level], list[Level]]:
        """``(bids, asks)``: the best ``n`` levels per side, best first.

        Returns fewer than ``n`` when a side is thinner than that; never pads.
        """
        raise NotImplementedError("OrderBook.top")

    def cumulative_qty(self, side: str, price_limit: float) -> float:
        """Total resting size on ``side`` at prices at least as good as ``price_limit``.

        ``side`` is ``"bid"`` or ``"ask"``. For bids that means prices ``>=
        price_limit``; for asks, ``<= price_limit``. This is the primitive the TCA
        layer's book walk is built on.
        """
        raise NotImplementedError("OrderBook.cumulative_qty")

    def checksum(self) -> int:
        """CRC32 over the top 10 levels per side, per the algorithm in the module docstring.

        Returns an unsigned 32-bit integer comparable to ``frame.checksum``.
        """
        raise NotImplementedError("OrderBook.checksum")

    def is_crossed(self) -> bool:
        """True when ``best_bid >= best_ask`` — always a bug in the replica."""
        raise NotImplementedError("OrderBook.is_crossed")

    def __len__(self) -> int:
        """Total number of levels held across both sides."""
        raise NotImplementedError("OrderBook.__len__")
