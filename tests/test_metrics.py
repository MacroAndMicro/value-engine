import math

import numpy as np
import pandas as pd
import pytest

from conftest import make_financials
from value_engine.engine.metrics import compute_metrics, consistency, trend_slope


def test_hand_calculated_two_years():
    df = pd.DataFrame({
        "pbt": [90.0, 140.0], "interest": [10.0, 10.0], "net_profit": [60.0, 100.0],
        "equity_capital": [100.0, 100.0], "reserves": [300.0, 500.0], "borrowings": [100.0, 100.0],
        "cfo": [80.0, 70.0], "capex": [30.0, 20.0],
        "other_assets": [400.0, 400.0], "other_liabilities": [150.0, 200.0], "opm_pct": [20.0, 22.0],
    }, index=pd.Index([2024, 2025], name="fiscal_year"))
    m = compute_metrics(df)

    # FY2025: EBIT = 140 + 10 = 150; capital employed avg of 500 and 700 = 600
    assert m.loc[2025, "roce_pct"] == pytest.approx(25.0)
    # First year has no opening balance -> uses closing: 100 / 500
    assert m.loc[2024, "roce_pct"] == pytest.approx(20.0)
    # ROE FY2025: 100 / avg(400, 600) = 20%
    assert m.loc[2025, "roe_pct"] == pytest.approx(20.0)
    assert m.loc[2025, "fcf"] == 50
    assert m.loc[2025, "debt_to_equity"] == pytest.approx(100 / 600)
    assert m.loc[2025, "interest_coverage"] == pytest.approx(15.0)
    assert m.loc[2025, "net_current_assets"] == 200
    assert m.loc[2025, "cfo_to_np"] == pytest.approx(0.7)
    # Profit rose 60 -> 100 while CFO fell 80 -> 70: divergence
    assert bool(m.loc[2025, "divergence"])
    assert not bool(m.loc[2024, "divergence"])


def test_net_current_assets_from_balance_sheet_breakdown():
    df = make_financials(years=2, inventories=50.0, trade_receivables=300.0, cash_equivalents=100.0,
                         trade_payables=120.0, other_liability_items=80.0,
                         non_controlling_interest=999.0)  # equity, must be ignored
    m = compute_metrics(df)
    # Liquid assets 450 (loans & advances missing -> skipped) minus liabilities 200
    assert (m["net_current_assets"] == 250.0).all()


def test_share_count_from_profit_and_eps():
    m = compute_metrics(make_financials())
    assert m["shares_cr"].round(6).eq(10.0).all()


def test_zero_interest_is_infinite_coverage():
    m = compute_metrics(make_financials(interest=0.0))
    assert np.isinf(m["interest_coverage"]).all()


def test_missing_columns_become_nan_not_zero():
    df = make_financials().drop(columns=["capex"])
    m = compute_metrics(df)
    assert m["fcf"].isna().all()


def test_gross_margin_preferred_over_opm():
    m = compute_metrics(make_financials(gross_margin_pct=40.0))
    assert (m["margin_pct"] == 40.0).all()


def test_consistency_and_trend():
    series = pd.Series([10.0, 20.0, 30.0, 40.0], index=[2021, 2022, 2023, 2024])
    stats = consistency(series, threshold=15)
    assert stats["years_above"] == 3
    assert stats["mean"] == 25
    assert trend_slope(series) == pytest.approx(10.0)
    assert math.isnan(trend_slope(pd.Series([5.0], index=[2024])))
