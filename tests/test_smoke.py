"""Smoke test: the package imports and settings load with defaults."""

from __future__ import annotations

from market_observer import __version__
from market_observer.config import Settings


def test_version() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_settings_defaults() -> None:
    # Construct with no env so defaults apply (ignore any real .env on disk).
    s = Settings(_env_file=None)
    assert s.watchlist_size == 10
    assert s.pinned_symbol_list == ["SPY", "QQQ"]
    assert s.deepseek_api_key is None
