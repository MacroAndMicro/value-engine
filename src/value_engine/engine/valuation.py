"""Price-based metrics: is a good business also available at a sensible price?

Pure functions. Inputs are the windowed metrics, company meta (TTM EPS and Screener's
headline price), an optional live quote and optional fiscal-year-end prices.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from value_engine import config
from value_engine.engine.metrics import cagr
from value_engine.models import CompanyMeta


@dataclass
class Valuation:
    price: float
    market_cap_cr: float
    price_source: str                 # "yfinance (live)" | "Screener (cached)"
    eps_ttm: float
    pe: float
    earnings_yield_pct: float
    pe_history: pd.Series             # P/E at each fiscal-year end
    pe_median: float
    pb: float
    pb_history: pd.Series
    pb_median: float
    graham_number: float
    fcf_base_cr: float                # avg FCF of the last 3 years: the reverse-DCF starting point
    fcf_yield_pct: float
    enterprise_value_cr: float
    implied_growth_pct: float         # FCF growth the current price assumes for the next 10 yrs
    profit_cagr_pct: float
    sales_cagr_pct: float


def reverse_dcf(value: float, base_cash_flow: float,
                discount_rate: float = config.DISCOUNT_RATE,
                terminal_growth: float = config.TERMINAL_GROWTH,
                years: int = config.DCF_YEARS) -> float:
    """Annual growth g (fraction) at which discounted future cash flows equal `value`.

    Cash flow grows at g for `years`, then at `terminal_growth` forever. Solved by
    bisection because present value rises steadily with g. NaN if no g in
    [-50%, +100%] fits, or the starting cash flow is not positive.
    """
    if not (base_cash_flow > 0 and value > 0):
        return math.nan

    def present_value(g: float) -> float:
        total, cash = 0.0, base_cash_flow
        for t in range(1, years + 1):
            cash *= 1 + g
            total += cash / (1 + discount_rate) ** t
        terminal = cash * (1 + terminal_growth) / (discount_rate - terminal_growth)
        return total + terminal / (1 + discount_rate) ** years

    low, high = -0.5, 1.0
    if not present_value(low) <= value <= present_value(high):
        return math.nan
    for _ in range(100):
        mid = (low + high) / 2
        if present_value(mid) < value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def compute_valuation(metrics: pd.DataFrame, meta: CompanyMeta,
                      quote: dict[str, float] | None,
                      fy_end_prices: pd.Series | None) -> Valuation | None:
    if quote:
        price, market_cap, source = quote["price"], quote["market_cap_cr"], "yfinance (live)"
    elif meta.snapshot.get("price") and meta.snapshot.get("market_cap_cr"):
        price, market_cap = meta.snapshot["price"], meta.snapshot["market_cap_cr"]
        source = f"Screener ({meta.fetched_at:%Y-%m-%d})"
    else:
        return None

    latest = metrics.ffill().iloc[-1]
    eps_ttm = meta.ttm.get("eps", math.nan)
    if not eps_ttm > 0:
        eps_ttm = latest["eps"]
    pe = price / eps_ttm if eps_ttm > 0 else math.nan

    shares_now = market_cap / price  # crore shares
    bvps = latest["book_value"] / shares_now
    pb = price / bvps if bvps > 0 else math.nan

    if fy_end_prices is not None:
        prices = fy_end_prices.reindex(metrics.index)
        pe_history = prices / metrics["eps"].where(metrics["eps"] > 0)
        pb_history = prices / (metrics["book_value"] / metrics["shares_cr"]).where(metrics["book_value"] > 0)
    else:
        pe_history = pb_history = pd.Series(np.nan, index=metrics.index, dtype=float)

    graham = math.sqrt(config.GRAHAM_NUMBER_FACTOR * eps_ttm * bvps) if eps_ttm > 0 and bvps > 0 else math.nan

    fcf = metrics["fcf"].dropna()
    fcf_base = float(fcf.tail(3).mean()) if len(fcf) else math.nan
    cash = latest["cash_equivalents"] if not pd.isna(latest["cash_equivalents"]) else 0.0
    debt = latest["borrowings"] if not pd.isna(latest["borrowings"]) else 0.0
    enterprise_value = market_cap + debt - cash
    if meta.is_financial:  # deposits and loans are a bank's raw material, not debt to repay
        fcf_base = enterprise_value = math.nan
    implied = reverse_dcf(enterprise_value, fcf_base) if not meta.is_financial else math.nan

    return Valuation(
        price=price, market_cap_cr=market_cap, price_source=source,
        eps_ttm=eps_ttm, pe=pe, earnings_yield_pct=100 / pe if pe > 0 else math.nan,
        pe_history=pe_history, pe_median=_median(pe_history),
        pb=pb, pb_history=pb_history, pb_median=_median(pb_history),
        graham_number=graham,
        fcf_base_cr=fcf_base,
        fcf_yield_pct=math.nan if math.isnan(fcf_base) else fcf_base / market_cap * 100,
        enterprise_value_cr=enterprise_value,
        implied_growth_pct=implied * 100,
        profit_cagr_pct=cagr(metrics["net_profit"]) * 100,
        sales_cagr_pct=cagr(metrics["sales"]) * 100,
    )


def _median(series: pd.Series) -> float:
    positive = series[series > 0].dropna()
    return float(positive.median()) if len(positive) else math.nan
