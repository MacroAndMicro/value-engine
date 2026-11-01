from datetime import datetime, timezone

from conftest import FIXTURES
from value_engine.ingestion.screener import ScreenerClient, company_url, schedule_url
from value_engine.storage.db import Database

_SCHEDULES = {
    ("Cash from Investing Activity", "cash-flow"): "tcs_capex_schedule.json",
    ("Expenses", "profit-loss"): "tcs_expenses_schedule.json",
    ("Other Assets", "balance-sheet"): "tcs_other_assets_schedule.json",
    ("Other Liabilities", "balance-sheet"): "tcs_other_liabilities_schedule.json",
    ("Borrowings", "balance-sheet"): "tcs_borrowings_schedule.json",
}


def _seeded_db() -> Database:
    """Pre-fill the HTTP cache so the client never touches the network."""
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    pages = {company_url("TCS", "consolidated"): "tcs.html"}
    for (parent, section), filename in _SCHEDULES.items():
        pages[schedule_url("3365", parent, section, "consolidated")] = filename
    for url, filename in pages.items():
        db.save_page(url, "TCS", "consolidated", (FIXTURES / filename).read_text(encoding="utf-8"), now)
    return db


def test_fetch_from_cache_merges_schedules_and_round_trips_storage():
    db = _seeded_db()
    fin = ScreenerClient(db).fetch("TCS")
    meta = fin.meta

    assert meta.basis == "consolidated"
    assert meta.margin_basis == "operating"  # TCS reports ~0% material cost
    assert meta.fy_month == 3
    assert meta.snapshot["price"] == 2503
    assert meta.snapshot["market_cap_cr"] == 905463
    assert meta.ttm["eps"] > 0

    capex = fin.statements["cash_flow"]["capex"]
    assert capex.loc[2015] == 2949  # outflow stored as a positive CapEx figure
    bs = fin.statements["balance_sheet"]
    assert {"trade_receivables", "cash_equivalents", "trade_payables", "lease_liabilities"} <= set(bs.columns)
    assert 2026 in bs.index and bs.loc[2026, "lease_liabilities"] == 11283  # Sep half-year dropped
    assert not any("approximated as Other Assets" in note for note in meta.notes)

    db.save_financials(fin)
    loaded = db.load_statements("TCS", "consolidated")
    assert loaded["profit_loss"].loc[2020, "sales"] == fin.statements["profit_loss"].loc[2020, "sales"]
