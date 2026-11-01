"""One ticker in, one complete Analysis out: fetch -> store -> metrics -> valuation -> rules."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from value_engine import config
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, RuleResult, evaluate
from value_engine.engine.valuation import Valuation, compute_valuation
from value_engine.ingestion.screener import ScreenerClient
from value_engine.ingestion.tickers import to_screener_symbol
from value_engine.ingestion.yahoo import fetch_fy_end_prices, fetch_quote
from value_engine.models import CompanyMeta


@dataclass
class Analysis:
    ticker: str
    meta: CompanyMeta
    metrics: pd.DataFrame          # last config.YEARS fiscal years
    valuation: Valuation | None
    results: list[RuleResult]


def analyze(ticker: str, client: ScreenerClient, *, standalone: bool = False,
            live_prices: bool = True) -> Analysis:
    fin = client.fetch(to_screener_symbol(ticker), prefer_consolidated=not standalone)
    client.db.save_financials(fin)
    # Compute on all years (ROCE needs the prior year's capital), then judge the last N.
    metrics = compute_metrics(fin.data).tail(config.YEARS)
    quote = fetch_quote(ticker) if live_prices else None
    fy_prices = fetch_fy_end_prices(ticker, fin.meta.fy_month) if live_prices else None
    valuation = compute_valuation(metrics, fin.meta, quote, fy_prices)
    results = evaluate(RuleContext(metrics, fin.meta, valuation))
    return Analysis(ticker, fin.meta, metrics, valuation, results)
