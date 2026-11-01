import json
import math

from conftest import FIXTURES
from value_engine.ingestion import normalize
from value_engine.ingestion.screener import parse_page
from value_engine.ingestion.tickers import to_screener_symbol, to_yahoo_symbol


def test_parse_number():
    assert normalize.parse_number("1,23,456") == 123456
    assert normalize.parse_number("27%") == 27
    assert normalize.parse_number("-2,949") == -2949
    assert math.isnan(normalize.parse_number(""))
    assert math.isnan(normalize.parse_number("--"))


def test_parse_header():
    assert normalize.parse_header("Mar 2024") == (3, 2024)
    assert normalize.parse_header("TTM") is None
    assert normalize.parse_header("") is None


def test_canonical_labels():
    assert normalize.canonical("Sales\xa0+") == "sales"
    assert normalize.canonical("Net Profit +") == "net_profit"
    assert normalize.canonical("Borrowing") == "borrowings"
    assert normalize.canonical("ROCE %") == "roce_pct_reported"


def test_normalize_drops_ttm_and_half_year_columns():
    headers = ["Mar 2024", "Mar 2025", "Sep 2025", "TTM"]
    rows = {"Reserves": ["100", "", "130", "999"]}
    df = normalize.normalize_statement(headers, rows, fy_month=3)
    assert list(df.index) == [2024, 2025]
    assert df.loc[2024, "reserves"] == 100
    assert math.isnan(df.loc[2025, "reserves"])  # missing stays NaN, never 0


def test_tickers():
    assert to_screener_symbol("tcs.ns") == "TCS"
    assert to_screener_symbol("500325.BO") == "500325"
    assert to_yahoo_symbol("INFY") == "INFY.NS"
    assert to_yahoo_symbol("500325") == "500325.BO"


def test_parse_real_tcs_page():
    statements, info = parse_page((FIXTURES / "tcs.html").read_text(encoding="utf-8"))
    assert info["name"] == "Tata Consultancy Services Ltd"
    assert info["company_id"] == "3365"
    assert info["fy_month"] == 3
    assert not info["is_financial"]
    assert set(statements) == {"profit_loss", "balance_sheet", "cash_flow", "ratios"}
    pl, bs = statements["profit_loss"], statements["balance_sheet"]
    assert len(pl) >= 10
    assert list(pl.index) == list(bs.index)  # TTM and "Sep" half-year columns removed
    for column in ("sales", "net_profit", "interest", "pbt", "opm_pct"):
        assert column in pl
    assert {"cfo", "fcf_reported"} <= set(statements["cash_flow"].columns)
    assert statements["ratios"]["roce_pct_reported"].notna().all()


def test_parse_real_bank_page_is_financial():
    statements, info = parse_page((FIXTURES / "hdfcbank.html").read_text(encoding="utf-8"))
    assert info["is_financial"]
    assert {"deposits", "borrowings"} <= set(statements["balance_sheet"].columns)


def test_schedule_series_from_real_capex_json():
    data = json.loads((FIXTURES / "tcs_capex_schedule.json").read_text())
    capex = normalize.schedule_series(data["Fixed assets purchased"], fy_month=3)
    assert capex.loc[2015] == -2949
    assert (capex < 0).all()
