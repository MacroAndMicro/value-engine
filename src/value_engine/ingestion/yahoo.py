"""Prices from yfinance: the current quote and fiscal-year-end closes.
Optional: every failure returns None and the report falls back to Screener's price."""
from __future__ import annotations

import warnings

import pandas as pd

from value_engine.ingestion.tickers import to_yahoo_symbol

_CRORE = 1e7


def fetch_quote(ticker: str) -> dict[str, float] | None:
    try:
        import yfinance as yf

        with warnings.catch_warnings():  # yfinance emits pandas deprecation noise
            warnings.simplefilter("ignore")
            info = yf.Ticker(to_yahoo_symbol(ticker)).fast_info
            price = float(info["last_price"])
            market_cap = float(info["market_cap"])
    except Exception:  # network errors, delisted tickers, yfinance API changes
        return None
    return {"price": price, "market_cap_cr": market_cap / _CRORE}


def fetch_fy_end_prices(ticker: str, fy_month: int | None) -> pd.Series | None:
    """Closing price at each fiscal-year end, indexed by fiscal year.
    yfinance's Close is adjusted for splits and bonus issues, which matches
    Screener's restated EPS, so price / EPS gives a consistent historical P/E."""
    if fy_month is None:
        return None
    try:
        import yfinance as yf

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            history = yf.Ticker(to_yahoo_symbol(ticker)).history(
                period="15y", interval="1mo", auto_adjust=False)
    except Exception:
        return None
    if history.empty:
        return None
    # A monthly bar labelled e.g. 2024-03-01 closes on the last trading day of March.
    closes = history["Close"][history.index.month == fy_month]
    return pd.Series(closes.to_numpy(), index=closes.index.year, dtype=float).groupby(level=0).last()
