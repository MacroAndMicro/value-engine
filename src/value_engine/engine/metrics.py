"""Deterministic value-investing metrics. Pure functions: DataFrame in, numbers out.

Input is CompanyFinancials.data (fiscal year index, canonical columns, Rs Crores).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Graham's net current assets from Screener's balance-sheet breakdown. Only items that
# are clearly current are counted as assets; every "other" liability counts, so the
# figure errs on the conservative side. Minority interest is equity, not a liability.
_LIQUID_ASSETS = ["inventories", "trade_receivables", "cash_equivalents", "loans_advances"]
_CURRENT_LIABILITIES = ["trade_payables", "customer_advances", "other_liability_items"]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df else pd.Series(np.nan, index=df.index, dtype=float)


def _sum_present(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Row sum of the columns that exist; NaN only when every component is missing."""
    present = [c for c in columns if c in df]
    if not present:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[present].sum(axis=1, min_count=1)


def _average_with_prior(series: pd.Series) -> pd.Series:
    """Average of opening and closing balance; first year falls back to closing."""
    return ((series + series.shift(1)) / 2).fillna(series)


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """One row per fiscal year. Ratios are plain numbers; *_pct columns are percentages."""
    net_profit = _col(df, "net_profit")
    cfo = _col(df, "cfo")
    capex = _col(df, "capex")
    interest = _col(df, "interest")
    borrowings = _col(df, "borrowings")
    shareholders_equity = _col(df, "equity_capital") + _col(df, "reserves")
    capital_employed = shareholders_equity + borrowings
    ebit = _col(df, "pbt") + interest

    m = pd.DataFrame(index=df.index)
    m["sales"] = _col(df, "sales")
    m["net_profit"] = net_profit
    m["cfo"] = cfo
    m["capex"] = capex
    m["fcf"] = cfo - capex
    m["ebit"] = ebit
    m["capital_employed"] = capital_employed
    m["roce_pct"] = ebit / _average_with_prior(capital_employed) * 100
    m["roe_pct"] = net_profit / _average_with_prior(shareholders_equity) * 100
    m["borrowings"] = borrowings
    m["debt_to_equity"] = borrowings / shareholders_equity
    # Zero interest means no debt to service: coverage is infinite, not undefined.
    m["interest_coverage"] = np.where(interest == 0, np.inf, ebit / interest)
    m.loc[interest.isna(), "interest_coverage"] = np.nan
    m["lease_liabilities"] = _col(df, "lease_liabilities")
    m["cash_equivalents"] = _col(df, "cash_equivalents")
    if any(c in df for c in ("trade_receivables", "cash_equivalents")):
        m["net_current_assets"] = (_sum_present(df, _LIQUID_ASSETS)
                                   - _sum_present(df, _CURRENT_LIABILITIES))
    else:
        # Fallback approximation: "Other Assets/Liabilities" are mostly current items.
        m["net_current_assets"] = _col(df, "other_assets") - _col(df, "other_liabilities")
    m["cfo_to_np"] = cfo / net_profit
    m["margin_pct"] = _col(df, "gross_margin_pct") if "gross_margin_pct" in df else _col(df, "opm_pct")
    # Fisher red flag: reported profit rises while operating cash falls.
    m["divergence"] = (net_profit.diff() > 0) & (cfo.diff() < 0)

    # --- Earnings quality ---
    eps = _col(df, "eps")
    m["eps"] = eps
    # Screener restates EPS for bonus issues/splits, so profit / EPS is a consistent
    # share count (crore shares). It also rises when minority holders take a bigger
    # slice of group profit - both reduce what reaches each shareholder.
    m["shares_cr"] = net_profit / eps.where(eps > 0)
    m["book_value"] = shareholders_equity
    m["debtor_days"] = _col(df, "debtor_days")
    m["other_income_share_pct"] = _col(df, "other_income") / _col(df, "pbt") * 100
    m["tax_pct"] = _col(df, "tax_pct")

    # --- Banks / NBFCs (Screener: Revenue = interest earned, Interest = interest paid) ---
    total_assets = _col(df, "total_assets")
    m["deposits"] = _col(df, "deposits")
    m["roa_pct"] = net_profit / _average_with_prior(total_assets) * 100
    m["nii_to_assets_pct"] = (_col(df, "sales") - interest) / _average_with_prior(total_assets) * 100
    return m


def cagr(series: pd.Series) -> float:
    """Compound annual growth between the first and last valid years (fraction, e.g. 0.12).
    NaN when either end is not positive - growth from a loss is not meaningful."""
    values = series.replace([np.inf, -np.inf], np.nan).dropna()
    if len(values) < 2:
        return np.nan
    first, last = values.iloc[0], values.iloc[-1]
    years = values.index[-1] - values.index[0]
    if first <= 0 or last <= 0 or years <= 0:
        return np.nan
    return float((last / first) ** (1 / years) - 1)


def consistency(series: pd.Series, threshold: float) -> dict[str, float]:
    """How reliably a metric clears a bar, and how much it wobbles.
    cv = coefficient of variation (std / mean): below ~0.25 is steady."""
    values = series.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {"years": 0, "mean": np.nan, "years_above": 0, "pct_years_above": np.nan, "cv": np.nan}
    mean = float(values.mean())
    return {
        "years": len(values),
        "mean": mean,
        "years_above": int((values > threshold).sum()),
        "pct_years_above": float((values > threshold).mean() * 100),
        "cv": float(values.std(ddof=0) / abs(mean)) if mean else np.nan,
    }


def trend_slope(series: pd.Series) -> float:
    """Least-squares slope per year (units of the series, e.g. pct-points/year)."""
    values = series.dropna()
    if len(values) < 2:
        return np.nan
    return float(np.polyfit(values.index.astype(float), values.to_numpy(), 1)[0])
