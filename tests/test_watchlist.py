"""T-05: watchlist selection (pure) + provider-driven build."""

from __future__ import annotations

from market_observer.data.watchlist import build_watchlist, select_watchlist

from .conftest import FakeProvider


def test_select_ranks_by_volume_with_pinned_first() -> None:
    vols = {"AAA": 100.0, "BBB": 500.0, "CCC": 300.0, "DDD": 50.0}
    wl = select_watchlist(vols, pinned=["SPY", "QQQ"], size=4)
    assert wl[:2] == ["SPY", "QQQ"]
    # remaining filled by highest volume: BBB(500) > CCC(300)
    assert wl[2:] == ["BBB", "CCC"]


def test_select_dedupes_and_uppercases_pinned() -> None:
    wl = select_watchlist({"AAA": 1.0}, pinned=["spy", "SPY", " qqq "], size=5)
    assert wl[:2] == ["SPY", "QQQ"]


def test_select_pinned_exceeds_size_truncates() -> None:
    wl = select_watchlist({"AAA": 1.0}, pinned=["A", "B", "C"], size=2)
    assert wl == ["A", "B"]


def test_select_excludes_pinned_from_ranking() -> None:
    vols = {"SPY": 999.0, "BBB": 10.0}
    wl = select_watchlist(vols, pinned=["SPY"], size=2)
    assert wl == ["SPY", "BBB"]  # SPY not double-counted


def test_build_watchlist_via_provider() -> None:
    provider = FakeProvider(
        universe=["AAA", "BBB", "CCC"],
        avg_volumes={"AAA": 10.0, "BBB": 30.0, "CCC": 20.0},
    )
    wl = build_watchlist(provider, pinned=["SPY"], size=3)
    assert wl == ["SPY", "BBB", "CCC"]


def test_build_watchlist_empty_universe_falls_back_to_pinned() -> None:
    provider = FakeProvider(universe=[])
    wl = build_watchlist(provider, pinned=["SPY", "QQQ"], size=5)
    assert wl == ["SPY", "QQQ"]
