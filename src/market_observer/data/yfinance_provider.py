"""yfinance-backed MarketDataProvider (free, possibly delayed data).

Network-touching code; not unit-tested live. The pure logic it feeds
(indicators, options math, watchlist ranking) is tested separately with fakes.
Option/macro/event methods are added in T-06.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from market_observer.domain.models import EventInfo
from market_observer.domain.options_math import ExpiryChain, OptionQuote

from .provider import OHLCV

logger = logging.getLogger(__name__)


def _opt_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _rows_to_quotes(df: object) -> list[OptionQuote]:
    quotes: list[OptionQuote] = []
    try:
        records = df.to_dict("records")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return quotes
    for row in records:
        strike = _opt_float(row.get("strike"))
        if strike is None:
            continue
        quotes.append(
            OptionQuote(
                strike=strike,
                implied_vol=_opt_float(row.get("impliedVolatility")),
                volume=_opt_float(row.get("volume")),
                open_interest=_opt_float(row.get("openInterest")),
            )
        )
    return quotes

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

    def get_option_expiries(self, symbol: str) -> list[date]:
        try:
            import yfinance as yf

            raw = yf.Ticker(symbol).options or ()
            return [date.fromisoformat(d) for d in raw]
        except Exception as exc:  # noqa: BLE001
            logger.warning("option expiries fetch failed for %s: %s", symbol, exc)
            return []

    def get_option_chain(self, symbol: str, expiry: date) -> ExpiryChain | None:
        try:
            import yfinance as yf

            oc = yf.Ticker(symbol).option_chain(expiry.isoformat())
        except Exception as exc:  # noqa: BLE001
            logger.warning("option chain fetch failed for %s %s: %s", symbol, expiry, exc)
            return None
        return ExpiryChain(
            expiry=expiry,
            calls=_rows_to_quotes(oc.calls),
            puts=_rows_to_quotes(oc.puts),
        )

    def get_macro_quote(self, symbol: str) -> tuple[float | None, float | None]:
        hist = self.get_history(symbol, 7)
        if hist is None or not hist.closes:
            return None, None
        last = hist.closes[-1]
        if len(hist.closes) < 2:
            return last, None
        prev = hist.closes[-2]
        pct = (last / prev - 1.0) * 100.0 if prev else None
        return last, pct

    def get_event_info(self, symbol: str) -> EventInfo | None:
        try:
            import yfinance as yf

            cal = yf.Ticker(symbol).calendar
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar fetch failed for %s: %s", symbol, exc)
            return None
        if not isinstance(cal, dict):
            return None

        def _first_date(value: object) -> date | None:
            seq = value if isinstance(value, (list, tuple)) else [value]
            for item in seq:
                if isinstance(item, date):
                    return item
                try:
                    return date.fromisoformat(str(item))
                except (TypeError, ValueError):
                    continue
            return None

        earnings = _first_date(cal.get("Earnings Date"))
        ex_div = _first_date(cal.get("Ex-Dividend Date"))
        if earnings is None and ex_div is None:
            return None
        return EventInfo(next_earnings_date=earnings, next_ex_dividend_date=ex_div)
