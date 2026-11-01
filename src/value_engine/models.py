"""Data containers passed between ingestion, storage, engine and report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from value_engine import config


@dataclass
class CompanyMeta:
    symbol: str
    name: str
    basis: str                      # "consolidated" | "standalone"
    source_url: str
    fetched_at: datetime
    company_id: str | None = None   # Screener's internal id, used by the schedules API
    is_financial: bool = False      # banks / NBFCs: ROCE, D/E and FCF rules don't apply
    margin_basis: str = "operating" # "gross" when material-cost data exists, else OPM is used
    fy_month: int | None = None     # fiscal-year-end month (3 = March)
    ttm: dict[str, float] = field(default_factory=dict)       # trailing-twelve-month P&L items
    snapshot: dict[str, float] = field(default_factory=dict)  # Screener headline: price, market cap
    units: str = config.UNITS
    notes: list[str] = field(default_factory=list)  # approximations used, shown as footnotes


@dataclass
class CompanyFinancials:
    """Normalized statements. Each DataFrame is indexed by fiscal year (int),
    columns are canonical line-item keys (see ingestion.normalize.LABEL_MAP)."""

    meta: CompanyMeta
    statements: dict[str, pd.DataFrame]

    @property
    def data(self) -> pd.DataFrame:
        """All statements side by side on one fiscal-year index."""
        combined = pd.concat(self.statements.values(), axis=1).sort_index()
        return combined.loc[:, ~combined.columns.duplicated()]

    def valid_years(self, column: str) -> int:
        data = self.data
        return int(data[column].notna().sum()) if column in data else 0
