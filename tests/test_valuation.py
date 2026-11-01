import math

import pandas as pd
import pytest

from conftest import make_financials
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, Status, evaluate
from value_engine.engine.valuation import compute_valuation, reverse_dcf


def _present_value(base, g, r=0.12, tg=0.05, years=10):
    total, cash = 0.0, base
    for t in range(1, years + 1):
        cash *= 1 + g
        total += cash / (1 + r) ** t
    return total + cash * (1 + tg) / (r - tg) / (1 + r) ** years


def test_reverse_dcf_recovers_growth():
    value = _present_value(100.0, 0.08)
    assert reverse_dcf(value, 100.0) == pytest.approx(0.08, abs=1e-6)


def test_reverse_dcf_rejects_negative_cash_flow_and_absurd_prices():
    assert math.isnan(reverse_dcf(1000.0, -5.0))
    assert math.isnan(reverse_dcf(1e12, 1.0))


def _valuation(meta, price, pe_history_price=None):
    metrics = compute_metrics(make_financials())
    quote = {"price": price, "market_cap_cr": price * 10}       # 10 crore shares
    prices = None
    if pe_history_price is not None:
        prices = pd.Series(pe_history_price, index=metrics.index, dtype=float)
    return metrics, compute_valuation(metrics, meta, quote, prices)


def test_valuation_numbers(meta):
    metrics, v = _valuation(meta, price=200.0, pe_history_price=metrics_eps_times(20))
    eps = metrics["eps"].iloc[-1]
    assert v.pe == pytest.approx(200.0 / eps)                     # no TTM -> latest FY EPS
    assert v.pe_median == pytest.approx(20.0)
    bvps = metrics["book_value"].iloc[-1] / 10
    assert v.graham_number == pytest.approx(math.sqrt(22.5 * eps * bvps))
    assert v.profit_cagr_pct == pytest.approx(10.0)
    assert v.enterprise_value_cr == pytest.approx(2000.0 + 100.0)  # mcap + debt, no cash data


def metrics_eps_times(multiple):
    return (compute_metrics(make_financials())["eps"] * multiple).tolist()


def test_cheap_stock_passes_valuation_rules(meta):
    metrics, v = _valuation(meta, price=150.0, pe_history_price=metrics_eps_times(25))
    results = {r.name: r for r in evaluate(RuleContext(metrics, meta, v))}
    assert v.pe < 15
    assert results["Graham P/E ceiling"].status is Status.PASS
    assert results["Cheaper than own history"].status is Status.PASS
    assert results["Price implies achievable growth"].status is Status.PASS


def test_expensive_stock_fails_valuation_rules(meta):
    metrics, v = _valuation(meta, price=2000.0, pe_history_price=metrics_eps_times(20))
    results = {r.name: r for r in evaluate(RuleContext(metrics, meta, v))}
    assert results["Graham P/E ceiling"].status is Status.FAIL
    assert results["Cheaper than own history"].status is Status.FAIL
    assert results["Price implies achievable growth"].status is Status.FAIL


def test_no_price_history_means_no_data(meta):
    metrics, v = _valuation(meta, price=150.0)
    results = {r.name: r for r in evaluate(RuleContext(metrics, meta, v))}
    assert results["Cheaper than own history"].status is Status.INSUFFICIENT_DATA


def test_falls_back_to_screener_snapshot_price(meta):
    meta.snapshot = {"price": 100.0, "market_cap_cr": 1000.0}
    v = compute_valuation(compute_metrics(make_financials()), meta, quote=None, fy_end_prices=None)
    assert v.price == 100.0
    assert v.price_source.startswith("Screener")
