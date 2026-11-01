"""CLI.

  python -m value_engine TCS.NS [--refresh] [--standalone] [--no-quote]
  python -m value_engine TCS.NS --ai [--reports latest|3] [--provider NAME] [--model ID] [--yes]
  python -m value_engine --watchlist stocks.txt [--csv results.csv]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from value_engine import config
from value_engine.ai.client import AIError, Analyst
from value_engine.ai.providers import PROVIDERS, Provider, make_provider
from value_engine.ingestion.screener import ScreenerClient, ScreenerError
from value_engine.pipeline import Analysis, analyze
from value_engine.reports.fetch import ReportError
from value_engine.report.console import render, render_watchlist
from value_engine.report.research_view import render_plan, render_research, save_memo
from value_engine.report.summary import write_csv
from value_engine.research import estimate_research_cost, prepare_reports, run_research
from value_engine.storage.db import Database


def parse_watchlist(text: str) -> list[str]:
    """One ticker per line (commas also accepted); '#' starts a comment."""
    tickers = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        tickers += [t.strip() for t in line.split(",") if t.strip()]
    return tickers


def read_watchlist(path: Path) -> list[str]:
    return parse_watchlist(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="value_engine",
                                     description="10-year value-investing screen for NSE/BSE stocks")
    parser.add_argument("ticker", nargs="?", help="e.g. TCS.NS, RELIANCE.NS, 500325.BO")
    parser.add_argument("--watchlist", type=Path, help="text file of tickers, one per line")
    parser.add_argument("--csv", type=Path, help="also write a one-row-per-stock summary CSV")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    parser.add_argument("--standalone", action="store_true", help="use standalone statements")
    parser.add_argument("--no-quote", action="store_true",
                        help="skip yfinance (uses Screener's cached price; no historical P/E)")
    parser.add_argument("--ai", action="store_true",
                        help="Phase 2: an LLM reads the annual report(s) and writes a memo (uses API credit)")
    parser.add_argument("--reports", choices=["latest", "3"], default="latest",
                        help="annual reports to analyse with --ai: the latest one (default) or the last 3")
    parser.add_argument("--provider", choices=list(PROVIDERS), default=config.AI_PROVIDER,
                        help=f"LLM provider for --ai (default: {config.AI_PROVIDER})")
    parser.add_argument("--model", help="model ID for --ai (default: the provider's default in config.py)")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation for --ai")
    args = parser.parse_args(argv)
    if bool(args.ticker) == bool(args.watchlist):
        parser.error("give either a ticker or --watchlist FILE")
    if args.ai and args.watchlist:
        parser.error("--ai works with a single ticker for now")

    provider = None
    if args.ai:  # check the key now, not after a minute of downloading
        try:
            provider = make_provider(args.provider, args.model)
            provider.check_ready()
        except AIError as exc:
            Console().print(f"[red]Error:[/red] {exc}")
            return 1

    console = Console()
    tickers = read_watchlist(args.watchlist) if args.watchlist else [args.ticker]
    analyses: list[Analysis] = []
    failures: list[tuple[str, str]] = []
    db = Database(config.DB_PATH)
    try:
        client = ScreenerClient(db, refresh=args.refresh)
        for i, ticker in enumerate(tickers, start=1):
            with console.status(f"[{i}/{len(tickers)}] Analysing {ticker}..."):
                try:
                    analyses.append(analyze(ticker, client, standalone=args.standalone,
                                            live_prices=not args.no_quote))
                except ScreenerError as exc:
                    failures.append((ticker, str(exc)))

        if args.watchlist:
            render_watchlist(analyses, failures, console)
        elif analyses:
            render(analyses[0], console)
        else:
            console.print(f"[red]Error:[/red] {failures[0][1]}")
            return 1
        if args.csv and analyses:
            write_csv(analyses, args.csv)
            console.print(f"  Summary written to {args.csv}")
        if args.ai:
            return run_ai(analyses[0], client, db, console, provider, count=3 if args.reports == "3" else 1,
                          assume_yes=args.yes)
    finally:
        db.close()
    return 0 if analyses else 1


def run_ai(analysis: Analysis, client: ScreenerClient, db: Database, console: Console,
           provider: Provider, count: int, assume_yes: bool) -> int:
    console.rule("Phase 2: Annual report review")
    try:
        with console.status("Finding annual reports...") as status:
            reports, excerpts = prepare_reports(
                analysis, client, count,
                on_progress=lambda fy: status.update(f"Downloading and reading the FY{fy} annual report "
                                                     "(first time takes about a minute)..."))
        if len(reports) < count:
            console.print(f"[yellow]Only {len(reports)} annual report(s) available.[/yellow]")
        analyst = Analyst(db, provider)
        render_plan(excerpts, estimate_research_cost(excerpts, provider.model), analyst.label, console)
        if not assume_yes and not _confirm(console, provider.label):
            console.print(f"  Cancelled. Nothing was sent to {provider.label}.")
            return 0
        with console.status(f"{provider.label} is reading the report(s) and writing the memo..."):
            research = run_research(analyst, analysis, reports, excerpts)
    except (ReportError, AIError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    render_research(research, console)
    path = save_memo(research, config.MEMOS_DIR)
    console.print(f"\n  Memo saved to {path}")
    unpriced = f" + {analyst.unpriced_calls} call(s) at an unknown price" if analyst.unpriced_calls else ""
    console.print(f"  AI cost this run: ${analyst.spent_usd:.2f}{unpriced} "
                  f"(all-time, priced calls: ${db.total_ai_cost():.2f})\n", style="dim")
    return 0


def _confirm(console: Console, label: str) -> bool:
    if not sys.stdin.isatty():
        console.print("  [yellow]Not an interactive terminal: re-run with --yes to approve the cost.[/yellow]")
        return False
    try:
        answer = console.input(f"  Send to {label}? [y/N] ")
    except EOFError:  # input closed: treat as "no"
        return False
    return answer.strip().lower() in ("y", "yes")


if __name__ == "__main__":
    sys.exit(main())
