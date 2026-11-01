from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from value_engine.models import CompanyMeta

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def meta() -> CompanyMeta:
    return CompanyMeta(symbol="TEST", name="Test Ltd", basis="consolidated",
                       source_url="", fetched_at=datetime.now(timezone.utc))


def make_financials(years: int = 10, **overrides) -> pd.DataFrame:
    """A steady, debt-light company: Rs 1,000 Cr sales growing 10%/yr."""
    index = pd.Index(range(2016, 2016 + years), name="fiscal_year")
    growth = pd.Series([1.1 ** i for i in range(years)], index=index)
    df = pd.DataFrame({
        "sales": 1000 * growth,
        "pbt": 200 * growth,
        "interest": 10.0,
        "net_profit": 150 * growth,
        "opm_pct": 25.0,
        "equity_capital": 100.0,
        "reserves": 900 * growth,
        "borrowings": 100.0,
        "other_assets": 600 * growth,
        "other_liabilities": 300 * growth,
        "cfo": 160 * growth,
        "capex": 40 * growth,
        "eps": 15 * growth,          # 10 crore shares, constant
        "tax_pct": 25.0,
        "other_income": 10 * growth,
        "debtor_days": 60.0,
    }, index=index)
    for column, values in overrides.items():
        df[column] = values
    return df
