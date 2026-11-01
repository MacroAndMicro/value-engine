from conftest import make_financials
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, Status, evaluate, score, screen


def _results(meta, valuation=None, years=10, **overrides):
    metrics = compute_metrics(make_financials(years=years, **overrides))
    return {r.name: r for r in evaluate(RuleContext(metrics, meta, valuation))}


def test_quality_company_passes_every_quality_rule(meta):
    results = _results(meta)
    failed = [name for name, r in results.items()
              if r.school != "Valuation" and r.status is not Status.PASS]
    assert failed == []
    # No price given: valuation rules say NO DATA rather than FAIL
    assert results["Graham P/E ceiling"].status is Status.INSUFFICIENT_DATA


def test_single_loss_year_fails_graham(meta):
    profits = [150.0] * 10
    profits[4] = -20.0
    result = _results(meta, net_profit=profits)["No loss-making years"]
    assert result.status is Status.FAIL
    assert "2020" in result.reason


def test_cash_not_keeping_up_with_profit_fails_fisher(meta):
    assert _results(meta, cfo=[50.0] * 10)["Profits backed by cash"].status is Status.FAIL


def test_shrinking_margin_fails_fisher(meta):
    results = _results(meta, opm_pct=[30.0 - i for i in range(10)])
    assert results["Stable/expanding operating margin"].status is Status.FAIL


def test_heavy_debt_fails_leverage_and_coverage(meta):
    results = _results(meta, borrowings=5000.0, interest=150.0)
    assert results["Low debt-to-equity"].status is Status.FAIL
    assert results["Strong interest coverage"].status is Status.FAIL


def test_short_history_is_insufficient_not_fail(meta):
    assert _results(meta, years=5)["High average ROCE"].status is Status.INSUFFICIENT_DATA


def test_earnings_quality_red_flags(meta):
    results = _results(meta,
                       debtor_days=[60.0] * 9 + [120.0],       # receivables doubled
                       other_income=[100.0 * 1.1 ** i for i in range(10)],  # half of PBT
                       tax_pct=5.0,
                       eps=[15.0] * 10)                         # profit grew, EPS didn't
    for rule in ("Receivables under control", "Profit from core business",
                 "Normal tax rate", "Per-share growth keeps up"):
        assert results[rule].status is Status.FAIL, rule


def test_banks_get_bank_rules_and_skip_industrial_ones(meta):
    meta.is_financial = True
    years = range(10)
    results = _results(meta,
                       total_assets=[10000.0 * 1.15 ** i for i in years],
                       deposits=[8000.0 * 1.15 ** i for i in years],
                       net_profit=[150.0 * 1.15 ** i for i in years],
                       eps=[15.0 * 1.15 ** i for i in years])
    assert results["Low debt-to-equity"].status is Status.NOT_APPLICABLE
    assert results["Profit from core business"].status is Status.NOT_APPLICABLE
    assert results["High return on assets"].status is Status.PASS        # 1.5% ROA
    assert results["Growing deposit franchise"].status is Status.PASS     # 15% CAGR
    assert results["Price implies achievable growth"].status is Status.NOT_APPLICABLE


def test_screen_labels(meta):
    results = list(_results(meta).values())
    passed, judged = score(results)
    assert passed == judged == 16
    assert score(results, valuation=True) == (0, 0)
    assert screen(results) == "QUALITY, EXPENSIVE"  # no valuation evidence -> not "cheap"
