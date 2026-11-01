"""Ticker conventions: yfinance uses TCS.NS / 532540.BO, Screener uses TCS / 532540."""

_SUFFIXES = (".NS", ".BO")


def to_screener_symbol(ticker: str) -> str:
    symbol = ticker.strip().upper()
    for suffix in _SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def to_yahoo_symbol(ticker: str) -> str:
    symbol = ticker.strip().upper()
    if symbol.endswith(_SUFFIXES):
        return symbol
    # Numeric codes are BSE scrip codes; everything else is assumed to be NSE.
    return f"{symbol}.BO" if symbol.isdigit() else f"{symbol}.NS"
