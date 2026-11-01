"""Tables and labels for the web app, built from engine objects (no new financial logic)."""
from __future__ import annotations

import pandas as pd

from value_engine.engine.rules import RuleResult, Status
from value_engine.pipeline import Analysis
from value_engine.report.console import format_value, metric_rows

# Icon + label, never colour alone (status colours are reserved for state).
STATUS_LABEL = {
    Status.PASS: "✅ PASS",
    Status.FAIL: "❌ FAIL",
    Status.NOT_APPLICABLE: "➖ N/A",
    Status.INSUFFICIENT_DATA: "⚠️ NO DATA",
}
SCREEN_BADGE = {  # label -> st.badge colour
    "QUALITY + VALUE": "green",
    "QUALITY, EXPENSIVE": "orange",
    "MIXED": "orange",
    "WEAK": "red",
    "INSUFFICIENT DATA": "gray",
}
VERDICT_BOX = {"BUY": "success", "WATCHLIST": "warning", "AVOID": "error"}


def rules_frame(results: list[RuleResult]) -> pd.DataFrame:
    return pd.DataFrame([{"School": r.school, "Rule": r.name, "Result": STATUS_LABEL[r.status],
                          "Value": r.value, "Threshold": r.threshold, "Why": r.reason} for r in results])


def fundamentals_frame(a: Analysis) -> pd.DataFrame:
    """The CLI's 10-year table as a DataFrame: metric rows x FY columns, formatted strings."""
    data = a.metrics.copy()
    data["pe"] = a.valuation.pe_history if a.valuation else float("nan")
    rows = {label: [format_value(data.loc[y, column], kind) for y in data.index]
            for label, column, kind in metric_rows(a.meta)}
    return pd.DataFrame(rows, index=[f"FY{str(y)[2:]}" for y in data.index]).T


def rupees(value: float | None, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"₹{value:,.{decimals}f}"


def crores(value: float | None) -> str:
    return "-" if value is None or pd.isna(value) else f"₹{value:,.0f} Cr"


def pct(value: float | None, decimals: int = 1) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:.{decimals}f}%"


def number(value: float | None, decimals: int = 1) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:,.{decimals}f}"
