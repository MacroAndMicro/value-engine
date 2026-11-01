"""Flat one-row-per-stock summary, shared by the watchlist table and the CSV export."""
from __future__ import annotations

import csv
import math
from pathlib import Path

from value_engine.engine.metrics import consistency
from value_engine.engine.rules import Status, score, screen
from value_engine.pipeline import Analysis

COLUMNS = [
    "ticker", "name", "basis", "screen", "quality_passed", "quality_judged",
    "valuation_passed", "valuation_judged", "price", "pe", "pe_median_10y",
    "avg_roce_pct", "avg_roe_pct", "implied_growth_pct", "profit_cagr_pct", "failed_rules",
]


def summary_row(a: Analysis) -> dict[str, object]:
    q_pass, q_judged = score(a.results)
    v_pass, v_judged = score(a.results, valuation=True)
    v = a.valuation
    return {
        "ticker": a.ticker,
        "name": a.meta.name,
        "basis": a.meta.basis,
        "screen": screen(a.results),
        "quality_passed": q_pass,
        "quality_judged": q_judged,
        "valuation_passed": v_pass,
        "valuation_judged": v_judged,
        "price": _round(v.price if v else math.nan, 2),
        "pe": _round(v.pe if v else math.nan, 1),
        "pe_median_10y": _round(v.pe_median if v else math.nan, 1),
        # ROCE treats deposits as capital, so it says nothing about a lender.
        "avg_roce_pct": "" if a.meta.is_financial else _round(consistency(a.metrics["roce_pct"], 0)["mean"], 1),
        "avg_roe_pct": _round(consistency(a.metrics["roe_pct"], 0)["mean"], 1),
        "implied_growth_pct": _round(v.implied_growth_pct if v else math.nan, 1),
        "profit_cagr_pct": _round(v.profit_cagr_pct if v else math.nan, 1),
        "failed_rules": "; ".join(r.name for r in a.results if r.status is Status.FAIL),
    }


def rank_key(a: Analysis) -> tuple[float, float]:
    """Sort key for ranking stocks: business-quality share first, then valuation share."""
    q_pass, q_judged = score(a.results)
    v_pass, v_judged = score(a.results, valuation=True)
    return (q_pass / q_judged if q_judged else 0, v_pass / v_judged if v_judged else 0)


def write_csv(analyses: list[Analysis], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(summary_row(a) for a in analyses)


def _round(value: float, digits: int) -> float | str:
    return "" if value is None or math.isnan(value) else round(value, digits)
