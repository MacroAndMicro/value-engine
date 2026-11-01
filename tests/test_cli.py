from value_engine.__main__ import read_watchlist
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, evaluate
from value_engine.pipeline import Analysis
from value_engine.report.summary import summary_row

from conftest import make_financials


def test_read_watchlist(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# my list\nTCS.NS\n\nINFY.NS, ITC.NS  # comment\n", encoding="utf-8")
    assert read_watchlist(path) == ["TCS.NS", "INFY.NS", "ITC.NS"]


def test_summary_row_hides_roce_for_lenders(meta):
    meta.is_financial = True
    metrics = compute_metrics(make_financials())
    analysis = Analysis("X.NS", meta, metrics, None, evaluate(RuleContext(metrics, meta)))
    row = summary_row(analysis)
    assert row["avg_roce_pct"] == ""
    assert row["price"] == ""
