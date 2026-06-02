"""T-06: options/macro/event builders via FakeProvider."""

from __future__ import annotations

from datetime import date

from market_observer.data.events import build_event_info
from market_observer.data.macro import build_macro_context
from market_observer.data.options_data import build_options_signal
from market_observer.domain.models import EventInfo
from market_observer.domain.options_math import ExpiryChain, OptionQuote

from .conftest import FakeProvider


def test_build_options_signal_full() -> None:
    e1 = date(2026, 6, 19)
    e2 = date(2026, 7, 17)
    provider = FakeProvider(
        expiries={"AAPL": [e1, e2]},
        chains={
            ("AAPL", e1): ExpiryChain(
                expiry=e1,
                calls=[OptionQuote(strike=100, implied_vol=0.25, volume=50, open_interest=500)],
                puts=[OptionQuote(strike=100, implied_vol=0.27, volume=60, open_interest=600)],
            ),
            ("AAPL", e2): ExpiryChain(
                expiry=e2,
                calls=[OptionQuote(strike=100, implied_vol=0.28)],
                puts=[OptionQuote(strike=100, implied_vol=0.30)],
            ),
        },
    )
    sig = build_options_signal(provider, "AAPL", spot=100.0)
    assert sig.has_data is True
    assert sig.front_atm_iv is not None
    assert sig.next_atm_iv is not None


def test_build_options_signal_no_expiries() -> None:
    provider = FakeProvider(expiries={})
    sig = build_options_signal(provider, "ZZZZ", spot=100.0)
    assert sig.has_data is False
    assert sig.note == "no option expiries"


def test_build_options_signal_chain_unavailable() -> None:
    e1 = date(2026, 6, 19)
    provider = FakeProvider(expiries={"X": [e1]}, chains={})  # expiry listed, chain missing
    sig = build_options_signal(provider, "X", spot=100.0)
    assert sig.has_data is False
    assert sig.note == "option chain unavailable"


def test_build_macro_context() -> None:
    provider = FakeProvider(
        macro={"^VIX": (14.2, -3.1), "^TNX": (4.25, 0.5)},
    )
    ctx = build_macro_context(provider, date(2026, 6, 1))
    by_symbol = {q.symbol: q for q in ctx.quotes}
    assert by_symbol["^VIX"].value == 14.2
    assert by_symbol["^VIX"].pct_change_1d == -3.1
    # Instrument with no data still appears, as None.
    assert by_symbol["CL=F"].value is None


def test_build_event_info_fills_days_to_earnings() -> None:
    provider = FakeProvider(
        events={"AAPL": EventInfo(next_earnings_date=date(2026, 6, 11))},
    )
    info = build_event_info(provider, "AAPL", as_of=date(2026, 6, 1))
    assert info is not None
    assert info.days_to_earnings == 10


def test_build_event_info_none_when_absent() -> None:
    provider = FakeProvider(events={})
    assert build_event_info(provider, "X", as_of=date(2026, 6, 1)) is None
