"""T-04: options signal pure-function tests."""

from __future__ import annotations

from datetime import date

import pytest

from market_observer.domain.options_math import (
    ExpiryChain,
    OptionQuote,
    atm_iv,
    compute_options_signal,
    iv_skew,
    put_call_ratio,
    term_structure,
)


def _chain(expiry: date) -> ExpiryChain:
    # spot ~ 100. Calls/puts across strikes with IVs forming a skew.
    calls = [
        OptionQuote(strike=90, implied_vol=0.30, volume=10, open_interest=100),
        OptionQuote(strike=100, implied_vol=0.25, volume=50, open_interest=500),
        OptionQuote(strike=105, implied_vol=0.22, volume=20, open_interest=200),
        OptionQuote(strike=110, implied_vol=0.20, volume=5, open_interest=50),
    ]
    puts = [
        OptionQuote(strike=90, implied_vol=0.35, volume=40, open_interest=400),
        OptionQuote(strike=95, implied_vol=0.32, volume=30, open_interest=300),
        OptionQuote(strike=100, implied_vol=0.27, volume=60, open_interest=600),
        OptionQuote(strike=110, implied_vol=0.24, volume=10, open_interest=100),
    ]
    return ExpiryChain(expiry=expiry, calls=calls, puts=puts)


def test_atm_iv_averages_nearest() -> None:
    chain = _chain(date(2026, 6, 19))
    # nearest call to 100 is strike100 iv .25; nearest put is strike100 iv .27
    assert atm_iv(chain, 100.0) == pytest.approx((0.25 + 0.27) / 2)


def test_term_structure() -> None:
    assert term_structure(0.25, 0.22) == pytest.approx(-0.03)
    assert term_structure(None, 0.2) is None


def test_put_call_ratios() -> None:
    chain = _chain(date(2026, 6, 19))
    call_vol = 10 + 50 + 20 + 5
    put_vol = 40 + 30 + 60 + 10
    assert put_call_ratio(chain, "volume") == pytest.approx(put_vol / call_vol)
    call_oi = 100 + 500 + 200 + 50
    put_oi = 400 + 300 + 600 + 100
    assert put_call_ratio(chain, "open_interest") == pytest.approx(put_oi / call_oi)


def test_iv_skew_positive_when_puts_bid() -> None:
    chain = _chain(date(2026, 6, 19))
    # OTM put near 95 iv .32 ; OTM call near 105 iv .22 ; skew = .10
    sk = iv_skew(chain, 100.0, otm_pct=0.05)
    assert sk == pytest.approx(0.32 - 0.22)


def test_compute_signal_full() -> None:
    front = _chain(date(2026, 6, 19))
    nxt = ExpiryChain(
        expiry=date(2026, 7, 17),
        calls=[OptionQuote(strike=100, implied_vol=0.28, volume=5, open_interest=50)],
        puts=[OptionQuote(strike=100, implied_vol=0.30, volume=5, open_interest=50)],
    )
    sig = compute_options_signal("aapl", 100.0, [front, nxt])
    assert sig.symbol == "AAPL"
    assert sig.has_data is True
    assert sig.front_atm_iv == pytest.approx(0.26)
    assert sig.next_atm_iv == pytest.approx(0.29)
    assert sig.term_structure == pytest.approx(0.03)
    assert sig.term_structure_inverted is False
    assert sig.iv_skew == pytest.approx(0.10)


def test_compute_signal_inverted_front_higher() -> None:
    front = ExpiryChain(
        expiry=date(2026, 6, 19),
        calls=[OptionQuote(strike=100, implied_vol=0.40)],
        puts=[OptionQuote(strike=100, implied_vol=0.40)],
    )
    nxt = ExpiryChain(
        expiry=date(2026, 7, 17),
        calls=[OptionQuote(strike=100, implied_vol=0.25)],
        puts=[OptionQuote(strike=100, implied_vol=0.25)],
    )
    sig = compute_options_signal("X", 100.0, [front, nxt])
    assert sig.term_structure_inverted is True


def test_compute_signal_no_chain() -> None:
    sig = compute_options_signal("X", 100.0, [])
    assert sig.has_data is False
    assert sig.note is not None


def test_compute_signal_no_spot() -> None:
    sig = compute_options_signal("X", None, [_chain(date(2026, 6, 19))])
    assert sig.has_data is False


def test_compute_signal_thin_chain_flagged() -> None:
    thin = ExpiryChain(expiry=date(2026, 6, 19), calls=[], puts=[])
    sig = compute_options_signal("X", 100.0, [thin])
    assert sig.has_data is False
    assert sig.note == "option chain too thin"
