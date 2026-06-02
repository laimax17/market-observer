"""yfinance-backed MarketDataProvider (free, possibly delayed data).

Network-touching code; not unit-tested live. The pure logic it feeds
(indicators, options math, watchlist ranking) is tested separately with fakes.
Option/macro/event methods are added in T-06.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from .provider import OHLCV

logger = logging.getLogger(__name__)

# Fallback universe if the Wikipedia constituent fetch fails: liquid mega-caps.
_FALLBACK_UNIVERSE: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM",
    "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "COST", "ORCL", "BAC",
    "AMD", "NFLX", "CRM", "KO", "PEP", "ADBE", "CSCO", "INTC", "QCOM", "TXN",
    "DIS", "WFC", "GE", "CAT", "BA", "MU", "PLTR", "SMCI", "COIN", "UBER",
)


class YFinanceProvider:
    """Concrete provider over the yfinance library."""

    def get_history(self, symbol: str, lookback_days: int) -> OHLCV | None:
        try:
            import yfinance as yf

            df = yf.Ticker(symbol).history(period=f"{lookback_days}d", auto_adjust=True)
        except Exception as exc:  # noqa: BLE001 - network/lib errors degrade to None
            logger.warning("history fetch failed for %s: %s", symbol, exc)
            return None
        if df is None or df.empty:
            return None
        try:
            return OHLCV(
                closes=[float(x) for x in df["Close"].tolist()],
                highs=[float(x) for x in df["High"].tolist()],
                lows=[float(x) for x in df["Low"].tolist()],
                volumes=[float(x) for x in df["Volume"].tolist()],
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("history parse failed for %s: %s", symbol, exc)
            return None

    def get_sp500_universe(self) -> list[str]:
        try:
            import pandas as pd

            tables = pd.read_html(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            )
            symbols = tables[0]["Symbol"].astype(str).tolist()
            cleaned = [s.replace(".", "-").strip().upper() for s in symbols if s]
            if cleaned:
                return cleaned
        except Exception as exc:  # noqa: BLE001 - fall back to built-in list
            logger.warning("S&P 500 universe fetch failed: %s; using fallback", exc)
        return list(_FALLBACK_UNIVERSE)

    def get_avg_volume(self, symbols: Sequence[str], period: int) -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            import yfinance as yf

            data = yf.download(
                list(symbols),
                period=f"{period + 10}d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("batch volume download failed: %s", exc)
            return out

        for sym in symbols:
            try:
                vol = data[sym]["Volume"].dropna().tail(period)
                if len(vol) > 0:
                    out[sym.upper()] = float(vol.mean())
            except (KeyError, ValueError, TypeError):
                continue
        return out
