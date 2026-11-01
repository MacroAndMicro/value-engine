"""Terminal output: 10-year table, valuation panel, scorecard and watchlist summary."""
from __future__ import annotations

import math

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from value_engine import config
from value_engine.engine.rules import RuleResult, Status, score, screen
from value_engine.engine.valuation import Valuation
from value_engine.models import CompanyMeta
from value_engine.pipeline import Analysis
from value_engine.report.summary import rank_key, summary_row

_STATUS_STYLE = {
    Status.PASS: "bold green",
    Status.FAIL: "bold red",
    Status.NOT_APPLICABLE: "dim",
    Status.INSUFFICIENT_DATA: "yellow",
}
_SCREEN_STYLE = {
    "QUALITY + VALUE": "bold green",
    "QUALITY, EXPENSIVE": "bold yellow",
    "MIXED": "yellow",
    "WEAK": "bold red",
    "INSUFFICIENT DATA": "dim",
}


def format_value(value: float, kind: str) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if math.isinf(value):
        return "no debt"
    if kind == "money":
        return f"{value:,.0f}"
    if kind == "pct":
        return f"{value:.1f}"
    if kind == "x":
        return f"{value:.1f}x"
    return f"{value:.2f}"


def render(a: Analysis, console: Console | None = None) -> None:
    console = console or Console()
    console.print(_header(a.meta, a.valuation))
    console.print(_metric_table(a.meta, a.metrics, a.valuation))
    if a.valuation:
        console.print(_valuation_panel(a.meta, a.valuation))
    console.print(_scorecard(a.results))

    q_pass, q_judged = score(a.results)
    v_pass, v_judged = score(a.results, valuation=True)
    label = screen(a.results)
    console.print(Text.assemble(
        ("  Business quality: ", "bold"), f"{q_pass}/{q_judged}   ",
        ("Valuation: ", "bold"), f"{v_pass}/{v_judged}   ",
        ("Screen: ", "bold"), (label, _SCREEN_STYLE[label]),
        ("   (N/A and NO DATA rules excluded)", "dim")))
    if a.meta.notes:
        console.print(Text("\n  Notes on data:", style="bold"))
        for note in a.meta.notes:
            console.print(f"   - {note}", style="dim", highlight=False)
    console.print("\n  Quantitative screen only - annual-report red flags come in Phase 2. "
                  "Not investment advice.\n", style="dim italic")


def render_watchlist(analyses: list[Analysis], failures: list[tuple[str, str]],
                     console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title=f"Watchlist Screen ({len(analyses)} stocks)", header_style="bold cyan")
    for column, justify in [("Ticker", "left"), ("Company", "left"), ("Screen", "left"),
                            ("Quality", "right"), ("Valuation", "right"), ("P/E", "right"),
                            ("10y med P/E", "right"), ("Avg ROCE %", "right"),
                            ("Implied g %", "right"), ("Past g %", "right")]:
        table.add_column(column, justify=justify, no_wrap=column != "Company")

    for a in sorted(analyses, key=rank_key, reverse=True):
        row = summary_row(a)
        table.add_row(
            row["ticker"], row["name"], Text(row["screen"], style=_SCREEN_STYLE[row["screen"]]),
            f"{row['quality_passed']}/{row['quality_judged']}",
            f"{row['valuation_passed']}/{row['valuation_judged']}",
            *(str(row[k]) if row[k] != "" else "-" for k in
              ("pe", "pe_median_10y", "avg_roce_pct", "implied_growth_pct", "profit_cagr_pct")))
    console.print(table)
    for ticker, error in failures:
        console.print(f"  [red]{ticker}: {error}[/red]", highlight=False)
    console.print("  Implied g = FCF growth the current price assumes; Past g = 10-yr profit CAGR.\n",
                  style="dim")


def _header(meta: CompanyMeta, valuation: Valuation | None) -> Panel:
    lines = [
        f"[bold]{meta.name}[/bold]  ({meta.symbol})",
        f"Statements: {meta.basis.title()}   Units: {meta.units}   "
        f"Data fetched: {meta.fetched_at:%Y-%m-%d}",
    ]
    if valuation:
        lines.append(f"Price: Rs {valuation.price:,.2f}   Market cap: Rs {valuation.market_cap_cr:,.0f} Cr"
                     f"   [dim](price: {valuation.price_source})[/dim]")
    if meta.is_financial:
        lines.append("[yellow]Bank/NBFC detected: industrial debt and cash-flow rules marked N/A; "
                     "bank rules added[/yellow]")
    return Panel("\n".join(lines), title="Value Engine", expand=False)


def metric_rows(meta: CompanyMeta) -> list[tuple[str, str, str]]:
    """(label, metrics column, format kind) for the 10-year table; shared with the web app.
    The "pe" column is valuation.pe_history, joined in by the caller."""
    if meta.is_financial:
        return [
            ("Revenue", "sales", "money"),
            ("Net Profit", "net_profit", "money"),
            ("Deposits", "deposits", "money"),
            ("ROA %", "roa_pct", "pct"),
            ("ROE %", "roe_pct", "pct"),
            ("NII / Assets %", "nii_to_assets_pct", "ratio"),
            ("EPS (Rs)", "eps", "ratio"),
            ("P/E at FY end", "pe", "pct"),
        ]
    margin_label = "Gross Margin %" if meta.margin_basis == "gross" else "Operating Margin %"
    return [
        ("Sales", "sales", "money"),
        ("Net Profit", "net_profit", "money"),
        ("Cash from Ops (CFO)", "cfo", "money"),
        ("CapEx", "capex", "money"),
        ("Free Cash Flow", "fcf", "money"),
        ("ROCE %", "roce_pct", "pct"),
        ("ROE %", "roe_pct", "pct"),
        ("Debt / Equity", "debt_to_equity", "ratio"),
        ("Interest Coverage", "interest_coverage", "x"),
        ("Net Current Assets", "net_current_assets", "money"),
        (margin_label, "margin_pct", "pct"),
        ("CFO / Net Profit", "cfo_to_np", "ratio"),
        ("Debtor Days", "debtor_days", "money"),
        ("P/E at FY end", "pe", "pct"),
    ]


def _metric_table(meta: CompanyMeta, metrics: pd.DataFrame, valuation: Valuation | None) -> Table:
    data = metrics.copy()
    data["pe"] = valuation.pe_history if valuation else math.nan
    years = list(data.index)
    table = Table(title=f"{len(years)}-Year Fundamentals ({meta.units})", header_style="bold cyan")
    table.add_column("Metric", no_wrap=True, style="bold")
    for year in years:
        table.add_column(f"FY{str(year)[2:]}", justify="right")

    diverged = data["divergence"].fillna(False) & (not meta.is_financial)
    for label, column, kind in metric_rows(meta):
        cells = []
        for year in years:
            value = data.loc[year, column]
            style = ""
            if column in ("net_profit", "cfo") and diverged[year]:
                style = "bold red"
            elif kind == "money" and not pd.isna(value) and value < 0:
                style = "red"
            cells.append(Text(format_value(value, kind), style=style))
        table.add_row(label, *cells)
    if not meta.is_financial:
        table.add_row("Divergence flag", *[Text("<!>", style="bold red") if diverged[y] else Text("")
                                           for y in years])
        table.caption = "<!> = profit grew while operating cash flow fell (red cells)"
    return table


def _valuation_panel(meta: CompanyMeta, v: Valuation) -> Table:
    table = Table(title="Valuation Snapshot", header_style="bold cyan", show_header=True)
    table.add_column("Measure", style="bold", no_wrap=True)
    table.add_column("Value", justify="right")
    table.add_column("What it means", style="dim")
    price_vs_graham = (v.price / v.graham_number - 1) * 100 if v.graham_number > 0 else math.nan
    rows = [
        ("P/E (trailing 12m)", format_value(v.pe, "pct"), f"10-yr median {format_value(v.pe_median, 'pct')}"),
        ("Earnings yield", f"{format_value(v.earnings_yield_pct, 'pct')}%", "Profit per Rs 100 invested; compare with FD rates"),
        ("P/B", format_value(v.pb, "ratio"), f"10-yr median {format_value(v.pb_median, 'ratio')}"),
        ("Graham Number", f"Rs {format_value(v.graham_number, 'money')}",
         "Max price Graham would pay; " + ("n/m" if math.isnan(price_vs_graham)
                                            else f"price is {price_vs_graham:+.0f}% vs it")),
    ]
    if not meta.is_financial:
        rows += [
            ("FCF yield (3-yr avg)", f"{format_value(v.fcf_yield_pct, 'pct')}%", "Free cash per Rs 100 of market cap"),
            ("Implied FCF growth", f"{format_value(v.implied_growth_pct, 'pct')}%",
             f"Reverse DCF: what the price assumes ({config.DISCOUNT_RATE:.0%} discount, "
             f"{config.DCF_YEARS} yrs, then {config.TERMINAL_GROWTH:.0%})"),
            ("Past growth (10-yr CAGR)",
             f"profit {format_value(v.profit_cagr_pct, 'pct')}% / sales {format_value(v.sales_cagr_pct, 'pct')}%",
             "What the business actually delivered"),
        ]
    for row in rows:
        table.add_row(*row)
    return table


def _scorecard(results: list[RuleResult]) -> Table:
    table = Table(title="Value Investing Scorecard", header_style="bold cyan")
    table.add_column("School", style="bold")
    table.add_column("Rule")
    table.add_column("Result", justify="center")
    table.add_column("Value", justify="right")
    table.add_column("Threshold")
    table.add_column("Why", style="dim")
    previous_school = None
    for r in results:
        if previous_school and r.school != previous_school:
            table.add_section()
        school = r.school if r.school != previous_school else ""
        previous_school = r.school
        table.add_row(school, r.name, Text(r.status.value, style=_STATUS_STYLE[r.status]),
                      r.value, r.threshold, r.reason)
    return table
