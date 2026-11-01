"""Terminal view and Markdown file for Phase 2 research (annual reports + memo)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from value_engine.ai.schemas import SEVERITY_ORDER, Severity
from value_engine.reports.sections import Excerpt
from value_engine.report.console import _STATUS_STYLE
from value_engine.research import Research

_VERDICT_STYLE = {"BUY": "bold green", "WATCHLIST": "bold yellow", "AVOID": "bold red"}
_SEVERITY_STYLE = {Severity.CRITICAL: "bold red", Severity.HIGH: "red",
                   Severity.MEDIUM: "yellow", Severity.LOW: "dim"}


def render_plan(excerpts: list[Excerpt], estimate_usd: float | None, model_label: str,
                console: Console) -> None:
    table = Table(title="Annual reports to analyse", header_style="bold cyan")
    for column in ("Report", "Statements", "Pages sent", "Sections", "~Tokens"):
        table.add_column(column)
    for e in excerpts:
        table.add_row(f"FY{e.fiscal_year}", e.basis, str(len(e.pages)),
                      ", ".join(e.sections), f"{len(e.text) // 4:,}")
    console.print(table)
    cost = ("unknown (add this model's price to AI_PRICES in config.py)" if estimate_usd is None
            else f"[bold]${estimate_usd:.2f}[/bold]")
    console.print(f"  Model: {model_label}   Estimated cost: {cost} "
                  "(reports analysed before with the same model are free - results are cached)")


def render_research(r: Research, console: Console) -> None:
    console.print(_checks_table(r))
    console.print(_flags_table(r))
    if r.cross_year:
        console.print(Text("  Year-over-year:", style="bold"))
        for note in r.cross_year:
            console.print(f"   - {note}", highlight=False)
    console.print(Panel(Markdown(memo_markdown(r, heading=False)),
                        title=Text(f"Investment Memo - {r.verdict}", style=_VERDICT_STYLE[r.verdict]),
                        expand=True))
    for note in r.notes:
        console.print(f"  - {note}", style="dim", highlight=False)


def _checks_table(r: Research) -> Table:
    table = Table(title="Annual Report Checks", header_style="bold cyan")
    for column in ("Check", "Result", "Value", "Threshold", "Why"):
        table.add_column(column, style="dim" if column == "Why" else None)
    for c in r.checks:
        table.add_row(c.name, Text(c.status.value, style=_STATUS_STYLE[c.status]), c.value, c.threshold, c.reason)
    return table


def _flags_table(r: Research) -> Table:
    table = Table(title=f"Red Flags Found by {r.model_label}", header_style="bold cyan")
    for column in ("Year", "Severity", "Category", "Finding", "Page", "Evidence"):
        table.add_column(column, no_wrap=column in ("Year", "Severity", "Page"))
    rows = 0
    for review in r.reviews:
        for f in sorted(review.flags, key=lambda f: SEVERITY_ORDER[f.flag.severity], reverse=True):
            evidence = Text("verified", style="green") if f.verified else Text("NOT FOUND - ignored", style="red")
            table.add_row(f"FY{review.findings.fiscal_year}",
                          Text(f.flag.severity.value, style=_SEVERITY_STYLE[f.flag.severity]),
                          f.flag.category.value.replace("_", " "), f"{f.flag.title}\n[dim]{f.flag.explanation}[/dim]",
                          str(f.page), evidence)
            rows += 1
    if not rows:
        table.add_row("-", "-", "-", "No red flags found", "-", "-")
    return table


def memo_markdown(r: Research, heading: bool = True) -> str:
    a, memo = r.analysis, r.memo
    lines = []
    if heading:
        lines += [f"# {a.meta.name} ({a.meta.symbol}): {r.verdict}", "",
                  f"*Generated {date.today():%d %b %Y} by Value Engine. Not investment advice.*", ""]
    lines += [f"**Verdict: {r.verdict}** (ceiling set by hard rules: {r.ceiling})   *Model: {r.model_label}*", "",
              f"> {memo.one_line_thesis}", "",
              "## Business quality", memo.business_quality, "",
              "## Financial strength", memo.financial_strength, "",
              "## Valuation", memo.valuation, "",
              "## Annual report review", memo.annual_report_review, "",
              "## Key risks", *[f"- {x}" for x in memo.key_risks], "",
              "## What would change my mind", *[f"- {x}" for x in memo.what_would_change_my_mind], "",
              "## Why the ceiling is " + r.ceiling, *[f"- {x}" for x in r.ceiling_reasons]]
    if heading:
        lines += ["", "## Verified red flags"]
        flags = [(rev.findings.fiscal_year, f) for rev in r.reviews for f in rev.verified_flags]
        lines += [f"- **FY{y} p.{f.page} ({f.flag.severity.value})** {f.flag.title}: {f.flag.explanation}\n"
                  f"  > \"{f.flag.evidence_quote}\"" for y, f in flags] or ["- None"]
    return "\n".join(lines)


def save_memo(r: Research, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{r.analysis.meta.symbol}-{date.today():%Y-%m-%d}.md"
    path.write_text(memo_markdown(r), encoding="utf-8")
    return path
