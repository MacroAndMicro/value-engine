"""Turn Screener's display strings into clean numeric DataFrames.

Rules: missing values stay NaN (never 0), the TTM column and half-year
balance-sheet columns (e.g. "Sep 2026") are dropped so every statement lines
up on the same fiscal-year index.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import pandas as pd

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_HEADER_RE = re.compile(r"^([A-Za-z]{3})[a-z]*\s+(\d{4})$")

# Screener display label (cleaned, lowercase) -> canonical key
LABEL_MAP = {
    # Profit & Loss
    "sales": "sales",
    "revenue": "sales",                       # banks
    "expenses": "expenses",
    "operating profit": "operating_profit",
    "financing profit": "operating_profit",   # banks
    "opm %": "opm_pct",
    "financing margin %": "opm_pct",          # banks
    "other income": "other_income",
    "interest": "interest",
    "depreciation": "depreciation",
    "profit before tax": "pbt",
    "tax %": "tax_pct",
    "net profit": "net_profit",
    "eps in rs": "eps",
    "dividend payout %": "dividend_payout_pct",
    # Balance Sheet
    "equity capital": "equity_capital",
    "reserves": "reserves",
    "borrowings": "borrowings",
    "borrowing": "borrowings",                # banks
    "deposits": "deposits",                   # banks
    "other liabilities": "other_liabilities",
    "total liabilities": "total_liabilities",
    "fixed assets": "fixed_assets",
    "cwip": "cwip",
    "investments": "investments",
    "other assets": "other_assets",
    "total assets": "total_assets",
    # Cash Flow
    "cash from operating activity": "cfo",
    "cash from investing activity": "cfi",
    "cash from financing activity": "cff",
    "net cash flow": "net_cash_flow",
    "free cash flow": "fcf_reported",
    # Ratios
    "debtor days": "debtor_days",
    "inventory days": "inventory_days",
    "days payable": "days_payable",
    "cash conversion cycle": "cash_conversion_cycle",
    "working capital days": "working_capital_days",
    "roce %": "roce_pct_reported",
    "roe %": "roe_pct_reported",
}


# Rows of Screener's schedules API (line-item breakdowns) -> canonical key
SCHEDULE_LABELS = {
    "fixed assets purchased": "fixed_assets_purchased",
    "material cost %": "material_cost_pct",
    # Other Assets breakdown
    "inventories": "inventories",
    "trade receivables": "trade_receivables",
    "cash equivalents": "cash_equivalents",
    "loans n advances": "loans_advances",
    "other asset items": "other_asset_items",
    # Other Liabilities breakdown
    "non controlling int": "non_controlling_interest",
    "trade payables": "trade_payables",
    "advance from customers": "customer_advances",
    "other liability items": "other_liability_items",
    # Borrowings breakdown
    "long term borrowings": "lt_borrowings",
    "short term borrowings": "st_borrowings",
    "lease liabilities": "lease_liabilities",
    "other borrowings": "other_borrowings",
}

# Screener's headline ratios (#top-ratios) -> snapshot key
TOP_RATIO_LABELS = {
    "market cap": "market_cap_cr",
    "current price": "price",
    "stock p/e": "pe",
    "book value": "book_value_per_share",
}


def clean_label(text: str) -> str:
    text = text.replace("\xa0", " ").replace("+", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def canonical(label: str) -> str:
    cleaned = clean_label(label)
    if cleaned in LABEL_MAP:
        return LABEL_MAP[cleaned]
    # Labels with tooltips ("CFO/EBITDA Share of operating ...") keep their first word.
    slug = cleaned.replace("%", " pct").split(" share of ")[0]
    return re.sub(r"[^a-z0-9]+", "_", slug).strip("_")


def parse_number(text: str | None) -> float:
    if text is None:
        return math.nan
    cleaned = text.replace(",", "").replace("%", "").replace("\xa0", "").strip()
    if cleaned in ("", "-", "--"):
        return math.nan
    try:
        return float(cleaned)
    except ValueError:
        return math.nan


def parse_first_number(text: str) -> float:
    """'₹ 9,05,463 Cr.' -> 905463.0 (Screener's headline values carry units)."""
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    return parse_number(match.group()) if match else math.nan


def parse_header(text: str) -> tuple[int, int] | None:
    """'Mar 2024' -> (3, 2024). Returns None for 'TTM' and blanks."""
    match = _HEADER_RE.match(text.strip())
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    return _MONTHS[match.group(1).lower()], int(match.group(2))


def dominant_month(headers: list[str]) -> int | None:
    """The fiscal-year-end month (3 = March for most Indian companies)."""
    months = Counter(p[0] for p in map(parse_header, headers) if p)
    return months.most_common(1)[0][0] if months else None


def normalize_statement(headers: list[str], rows: dict[str, list[str]],
                        fy_month: int | None) -> pd.DataFrame:
    """headers: column labels after the row-label column; rows: label -> cell strings."""
    keep: list[int] = []
    years: list[int] = []
    for i, header in enumerate(headers):
        parsed = parse_header(header)
        if parsed is None or (fy_month is not None and parsed[0] != fy_month):
            continue
        keep.append(i)
        years.append(parsed[1])

    data: dict[str, list[float]] = {}
    for label, cells in rows.items():
        key = canonical(label)
        if key and key not in data:
            data[key] = [parse_number(cells[i]) if i < len(cells) else math.nan for i in keep]

    frame = pd.DataFrame(data, index=pd.Index(years, name="fiscal_year"), dtype=float)
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def schedule_series(row: dict[str, str], fy_month: int | None) -> pd.Series:
    """One row of Screener's schedules JSON ({'Mar 2015': '-2,949', ...}) -> Series by year."""
    values: dict[int, float] = {}
    for header, cell in row.items():
        parsed = parse_header(header)
        if parsed and (fy_month is None or parsed[0] == fy_month):
            values[parsed[1]] = parse_number(cell)
    return pd.Series(values, dtype=float).sort_index()


def schedule_frame(data: dict, fy_month: int | None) -> pd.DataFrame:
    """Whole schedules JSON -> DataFrame (fiscal year x canonical line item)."""
    columns = {}
    for label, row in data.items():
        if isinstance(row, dict):
            cleaned = clean_label(label)
            columns[SCHEDULE_LABELS.get(cleaned, canonical(label))] = schedule_series(row, fy_month)
    return pd.DataFrame(columns)


_FINANCIAL_LABELS = {"deposits", "financing profit", "financing margin %"}


def is_financial(raw_labels: list[str]) -> bool:
    """Banks show Deposits on the balance sheet; banks/NBFCs show 'Financing Profit'."""
    return any(clean_label(label) in _FINANCIAL_LABELS for label in raw_labels)
