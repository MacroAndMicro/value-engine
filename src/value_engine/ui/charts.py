"""Altair charts for the web app.

Colours come from the validated reference palette (dataviz skill): categorical slots in
fixed order - a series keeps its colour on every chart - and status colours only for
state (a negative FCF year). Every chart has tooltips, and a table view of the same
numbers lives in the Data tab.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from value_engine import config

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]          # blue, orange, aqua (slots 1-3)
CRITICAL = "#d03b3b"                                # status: critical
GOOD = "#0ca30c"                                    # status: good
WARNING = "#fab219"                                 # status: warning
MUTED = "#898781"                                   # reference lines / annotations
_HEIGHT = 260


def _fy(index) -> list[str]:
    return [f"FY{str(y)[2:]}" for y in index]


def _long(metrics: pd.DataFrame, series: dict[str, str]) -> pd.DataFrame:
    """metrics -> rows of (FY, series label, value) for the given {label: column}."""
    frame = pd.DataFrame({label: metrics[col].to_numpy() for label, col in series.items()},
                         index=_fy(metrics.index))
    return frame.rename_axis("FY").reset_index().melt("FY", var_name="Series", value_name="Value").dropna()


def _color(labels: list[str]) -> alt.Color:
    return alt.Color("Series:N", scale=alt.Scale(domain=labels, range=SERIES[:len(labels)]),
                     legend=alt.Legend(orient="top", title=None))


def _x() -> alt.X:
    return alt.X("FY:O", title=None, axis=alt.Axis(labelAngle=0))


def grouped_bars(metrics: pd.DataFrame, series: dict[str, str], title: str, unit: str = "₹ Cr") -> alt.Chart:
    data = _long(metrics, series)
    labels = list(series)
    return (alt.Chart(data, title=title, height=_HEIGHT)
            .mark_bar(cornerRadiusEnd=4, stroke="transparent", strokeWidth=2)
            .encode(x=_x(), xOffset=alt.XOffset("Series:N", sort=labels),
                    y=alt.Y("Value:Q", title=unit), color=_color(labels),
                    tooltip=["FY", "Series", alt.Tooltip("Value:Q", format=",.0f", title=unit)]))


def profit_vs_cash(metrics: pd.DataFrame) -> alt.LayerChart:
    """Net profit and operating cash flow side by side; divergence years get a marker."""
    bars = grouped_bars(metrics, {"Net profit": "net_profit", "Cash from operations": "cfo"},
                        "Profit vs cash from operations")
    flagged = pd.DataFrame({"FY": _fy(metrics.index[metrics["divergence"].fillna(False).astype(bool)])})
    if flagged.empty:
        return bars
    top = float(metrics[["net_profit", "cfo"]].max().max())
    flagged["Value"] = top * 1.08
    flagged["Note"] = "Profit rose while operating cash fell"
    marks = (alt.Chart(flagged).mark_text(text="▲ divergence", color=CRITICAL, fontSize=11, fontWeight="bold")
             .encode(x=_x(), y="Value:Q", tooltip=["FY", "Note"]))
    return alt.layer(bars, marks)


def fcf_bars(metrics: pd.DataFrame) -> alt.Chart:
    data = pd.DataFrame({"FY": _fy(metrics.index), "FCF": metrics["fcf"].to_numpy()}).dropna()
    data["Sign"] = data["FCF"].map(lambda v: "Positive" if v > 0 else "Negative")
    return (alt.Chart(data, title="Free cash flow (CFO − CapEx)", height=_HEIGHT)
            .mark_bar(cornerRadiusEnd=4)
            .encode(x=_x(), y=alt.Y("FCF:Q", title="₹ Cr"),
                    color=alt.Color("Sign:N", scale=alt.Scale(domain=["Positive", "Negative"],
                                                              range=[SERIES[0], CRITICAL]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=["FY", alt.Tooltip("FCF:Q", format=",.0f", title="FCF ₹ Cr"), "Sign"]))


def lines(metrics: pd.DataFrame, series: dict[str, str], title: str, unit: str,
          reference: float | None = None, reference_label: str = "") -> alt.LayerChart | alt.Chart:
    data = _long(metrics, series)
    labels = list(series)
    chart = (alt.Chart(data, title=title, height=_HEIGHT)
             .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64, filled=True))
             .encode(x=_x(), y=alt.Y("Value:Q", title=unit, scale=alt.Scale(zero=False)),
                     color=_color(labels),
                     tooltip=["FY", "Series", alt.Tooltip("Value:Q", format=",.1f", title=unit)]))
    if reference is None or pd.isna(reference):
        return chart
    ref = pd.DataFrame({"y": [reference], "label": [reference_label]})
    rule = alt.Chart(ref).mark_rule(color=MUTED, strokeDash=[6, 4], strokeWidth=1.5).encode(
        y="y:Q", tooltip=["label"])
    text = alt.Chart(ref).mark_text(align="left", dx=4, dy=-7, color=MUTED, fontSize=11).encode(
        x=alt.value(0), y="y:Q", text="label")
    return alt.layer(chart, rule, text)


def returns_chart(metrics: pd.DataFrame) -> alt.LayerChart:
    return lines(metrics, {"ROCE %": "roce_pct", "ROE %": "roe_pct"}, "Return on capital", "%",
                 config.ROCE_MIN_PCT, f"{config.ROCE_MIN_PCT:.0f}% bar")


def pe_chart(pe_history: pd.Series, median: float) -> alt.LayerChart:
    frame = pe_history.to_frame("pe")
    return lines(frame, {"P/E at FY end": "pe"}, "P/E history", "×", median, f"10-yr median {median:.1f}")


_SCREEN_COLORS = {"QUALITY + VALUE": GOOD, "QUALITY, EXPENSIVE": WARNING, "MIXED": "#ec835a",
                  "WEAK": CRITICAL, "INSUFFICIENT DATA": MUTED}


def watchlist_scatter(rows: pd.DataFrame) -> alt.LayerChart:
    """Business quality vs valuation, one dot per stock, labelled with its ticker."""
    data = rows.assign(
        quality=rows["quality_passed"] / rows["quality_judged"].where(rows["quality_judged"] > 0) * 100,
        value=rows["valuation_passed"] / rows["valuation_judged"].where(rows["valuation_judged"] > 0) * 100,
    ).fillna({"quality": 0, "value": 0})
    domain = [s for s in _SCREEN_COLORS if s in set(data["screen"])]
    base = alt.Chart(data, title="Quality vs price: top-right is the sweet spot", height=340)
    dots = base.mark_circle(size=180, opacity=0.9, stroke="white", strokeWidth=2).encode(
        x=alt.X("quality:Q", title="Business-quality rules passed (%)", scale=alt.Scale(domain=[0, 105])),
        y=alt.Y("value:Q", title="Valuation rules passed (%)", scale=alt.Scale(domain=[-5, 105])),
        color=alt.Color("screen:N", title=None, legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=domain, range=[_SCREEN_COLORS[s] for s in domain])),
        tooltip=["ticker", "name", "screen", "pe", "avg_roce_pct"])
    labels = base.mark_text(dy=-14, fontSize=11).encode(x="quality:Q", y="value:Q", text="ticker")
    return alt.layer(dots, labels)
