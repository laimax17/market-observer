"""Executable specification for the parts left to implement by hand.

Every test here skips while the corresponding function still raises
``NotImplementedError``, and starts running the moment it does not. Read them as the
acceptance criteria for :mod:`l2tca.book`, :mod:`l2tca.signals` and :mod:`l2tca.tca`
— they encode the invariants the module docstrings describe, so an implementation
can be developed test-first with no live connection.

Run just this file while working on the core::

    uv run pytest tests/test_core_contract.py -v
"""

from __future__ import annotations

import pytest

from l2tca.book import OrderBook, SequenceError
from l2tca.feed.parser import BookFrame, Level


def call(fn, *args, **kwargs):
    """Call ``fn``, skipping the test if it is still a specified stub."""
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        pytest.skip(f"not implemented yet: {exc}")


def frame(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    snapshot: bool = False,
    checksum: int | None = None,
) -> BookFrame:
    return BookFrame(
        raw={},
        symbol="BTC/USD",
        is_snapshot=snapshot,
        bids=tuple(Level(price, qty) for price, qty in bids),
        asks=tuple(Level(price, qty) for price, qty in asks),
        checksum=checksum,
        exchange_ts_ns=None,
    )


SNAPSHOT = frame(
    bids=[(100.0, 1.0), (99.0, 2.0), (98.0, 3.0)],
    asks=[(101.0, 1.5), (102.0, 2.5), (103.0, 3.5)],
    snapshot=True,
)


@pytest.fixture
def book() -> OrderBook:
    book = OrderBook(symbol="BTC/USD", depth=3)
    call(book.apply_snapshot, SNAPSHOT)
    return book


# ------------------------------------------------------------------ the book


def test_snapshot_sets_the_top_of_book(book):
    assert call(book.best_bid) == Level(100.0, 1.0)
    assert call(book.best_ask) == Level(101.0, 1.5)


def test_top_of_book_derives_mid_and_spread(book):
    top = call(book.top_of_book)
    assert top.mid == pytest.approx(100.5)
    assert top.spread == pytest.approx(1.0)


def test_update_replaces_size_rather_than_adding_to_it(book):
    call(book.apply_update, frame(bids=[(100.0, 5.0)], asks=[]))
    assert call(book.best_bid) == Level(100.0, 5.0)


def test_zero_quantity_deletes_the_level(book):
    call(book.apply_update, frame(bids=[(100.0, 0.0)], asks=[]))
    assert call(book.best_bid) == Level(99.0, 2.0)


def test_deleting_a_level_that_is_not_held_is_not_an_error(book):
    # It may already have fallen off the local fixed-depth window.
    call(book.apply_update, frame(bids=[(1.0, 0.0)], asks=[]))
    assert call(book.best_bid) == Level(100.0, 1.0)


def test_insertion_improves_the_touch(book):
    call(book.apply_update, frame(bids=[(100.5, 0.5)], asks=[]))
    assert call(book.best_bid) == Level(100.5, 0.5)


def test_the_book_never_holds_more_than_depth_levels_per_side(book):
    call(book.apply_update, frame(bids=[(100.5, 0.5)], asks=[]))
    bids, _ = call(book.top, 10)
    assert len(bids) == 3, "a fixed-depth book must drop the far level itself"
    assert [level.price for level in bids] == [100.5, 100.0, 99.0]


def test_levels_stay_sorted_best_first(book):
    call(book.apply_update, frame(bids=[(99.5, 1.0)], asks=[(101.5, 1.0)]))
    bids, asks = call(book.top, 3)
    assert [level.price for level in bids] == sorted((b.price for b in bids), reverse=True)
    assert [level.price for level in asks] == sorted(a.price for a in asks)


def test_the_book_is_never_crossed(book):
    call(book.apply_update, frame(bids=[(99.5, 1.0)], asks=[(101.5, 1.0)]))
    assert call(book.is_crossed) is False


def test_every_stored_level_has_positive_size(book):
    call(book.apply_update, frame(bids=[(99.0, 0.0)], asks=[(102.0, 0.0)]))
    bids, asks = call(book.top, 10)
    assert all(level.qty > 0 for level in [*bids, *asks])


def test_a_delta_before_any_snapshot_is_a_sequence_error():
    fresh = OrderBook(symbol="BTC/USD", depth=3)
    try:
        with pytest.raises(SequenceError):
            fresh.apply_update(frame(bids=[(100.0, 1.0)], asks=[]))
    except NotImplementedError as exc:
        pytest.skip(f"not implemented yet: {exc}")


def test_a_snapshot_replaces_everything_that_came_before(book):
    call(book.apply_update, frame(bids=[(100.5, 9.0)], asks=[]))
    call(
        book.apply_snapshot,
        frame(bids=[(50.0, 1.0)], asks=[(51.0, 1.0)], snapshot=True),
    )
    assert call(book.best_bid) == Level(50.0, 1.0)
    assert len(book) == 2


def test_a_frame_for_another_symbol_is_rejected(book):
    other = BookFrame(
        raw={},
        symbol="ETH/USD",
        is_snapshot=True,
        bids=(Level(1.0, 1.0),),
        asks=(Level(2.0, 1.0),),
        checksum=None,
        exchange_ts_ns=None,
    )
    try:
        with pytest.raises(SequenceError):
            book.apply_snapshot(other)
    except NotImplementedError as exc:
        pytest.skip(f"not implemented yet: {exc}")


def test_cumulative_qty_counts_levels_at_or_better_than_the_limit(book):
    assert call(book.cumulative_qty, "bid", 99.0) == pytest.approx(3.0)  # 1.0 + 2.0
    assert call(book.cumulative_qty, "ask", 102.0) == pytest.approx(4.0)  # 1.5 + 2.5


def test_checksum_is_an_unsigned_32_bit_value(book):
    value = call(book.checksum)
    assert isinstance(value, int)
    assert 0 <= value <= 0xFFFFFFFF


def test_checksum_is_stable_for_unchanged_state(book):
    assert call(book.checksum) == call(book.checksum)


def test_checksum_changes_when_the_top_of_book_changes(book):
    before = call(book.checksum)
    call(book.apply_update, frame(bids=[(100.0, 7.0)], asks=[]))
    assert call(book.checksum) != before


# ---------------------------------------------------------------- the signals


def test_mid_price(book):
    from l2tca.signals import mid_price

    assert call(mid_price, book) == pytest.approx(100.5)


def test_microprice_sits_between_the_quotes_and_leans_to_the_thin_side(book):
    from l2tca.signals import microprice

    # Bid size 1.0, ask size 1.5: more resting size on the offer, so fair value
    # should sit below the mid.
    value = call(microprice, book)
    assert 100.0 < value < 101.0
    assert value < 100.5


def test_order_book_imbalance_is_bounded_and_signed(book):
    from l2tca.signals import order_book_imbalance

    value = call(order_book_imbalance, book, levels=1)
    assert -1.0 <= value <= 1.0
    assert value < 0, "the ask side is larger, so imbalance is negative"


def test_spread_in_basis_points(book):
    from l2tca.signals import spread_bps

    assert call(spread_bps, book) == pytest.approx(1.0 / 100.5 * 10_000)


def test_signals_return_none_on_an_empty_book():
    from l2tca.signals import mid_price

    empty = OrderBook(symbol="BTC/USD", depth=3)
    call(empty.apply_snapshot, frame(bids=[], asks=[], snapshot=True))
    assert call(mid_price, empty) is None


# ------------------------------------------------------------------- the TCA


def test_a_small_buy_fills_at_the_touch(book):
    from l2tca.tca import sweep

    result = call(sweep, book, "buy", 1.0)
    assert result.filled_qty == pytest.approx(1.0)
    assert result.vwap == pytest.approx(101.0)
    assert result.levels_consumed == 1


def test_a_larger_buy_walks_the_book(book):
    from l2tca.tca import sweep

    result = call(sweep, book, "buy", 3.0)  # 1.5 @ 101 + 1.5 @ 102
    assert result.filled_qty == pytest.approx(3.0)
    assert result.vwap == pytest.approx((1.5 * 101.0 + 1.5 * 102.0) / 3.0)
    assert result.levels_consumed == 2


def test_an_oversized_order_reports_the_residual_rather_than_raising(book):
    from l2tca.tca import sweep

    result = call(sweep, book, "buy", 100.0)
    assert result.filled_qty == pytest.approx(7.5)  # the whole visible ask side
    assert result.unfilled_qty == pytest.approx(92.5)


def test_a_limit_price_stops_the_walk(book):
    from l2tca.tca import sweep

    result = call(sweep, book, "buy", 5.0, limit_price=101.0)
    assert result.filled_qty == pytest.approx(1.5)
    assert result.unfilled_qty == pytest.approx(3.5)


def test_a_sell_consumes_the_bid_side(book):
    from l2tca.tca import sweep

    result = call(sweep, book, "sell", 2.0)
    assert result.vwap == pytest.approx((1.0 * 100.0 + 1.0 * 99.0) / 2.0)


def test_slippage_is_positive_when_the_fill_is_worse_than_the_reference(book):
    from l2tca.tca import slippage_bps, sweep

    buy = call(sweep, book, "buy", 3.0)
    assert call(slippage_bps, buy, 100.5) > 0, "a buy above the mid is a cost"

    sell = call(sweep, book, "sell", 2.0)
    assert call(slippage_bps, sell, 100.5) > 0, "a sell below the mid is also a cost"


def test_cost_curve_is_monotonic_in_size(book):
    from l2tca.tca import cost_curve

    curve = call(cost_curve, book, "buy", [0.5, 1.5, 3.0])
    costs = [cost for _, cost in curve]
    assert costs == sorted(costs), "walking deeper into the book cannot get cheaper"
