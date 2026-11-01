"""SQLite storage: an HTTP cache of raw pages plus normalized line items.

line_items is in long format (one row per company/year/item) so new line items
need no schema change, and it maps directly onto PostgreSQL later.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from value_engine.models import CompanyFinancials

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_pages (
    url         TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    basis       TEXT NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    symbol        TEXT NOT NULL,
    basis         TEXT NOT NULL,
    name          TEXT,
    is_financial  INTEGER NOT NULL,
    last_updated  TEXT NOT NULL,
    PRIMARY KEY (symbol, basis)
);
CREATE TABLE IF NOT EXISTS line_items (
    symbol       TEXT NOT NULL,
    basis        TEXT NOT NULL,
    statement    TEXT NOT NULL,
    fiscal_year  INTEGER NOT NULL,
    item         TEXT NOT NULL,
    value        REAL,
    PRIMARY KEY (symbol, basis, statement, fiscal_year, item)
);
CREATE TABLE IF NOT EXISTS ai_results (
    cache_key      TEXT PRIMARY KEY,   -- hash of provider + model + prompt + input
    symbol         TEXT NOT NULL,
    kind           TEXT NOT NULL,      -- "findings" | "memo"
    fiscal_year    INTEGER,
    model          TEXT NOT NULL,      -- "provider:model"
    prompt_version TEXT NOT NULL,
    output_json    TEXT NOT NULL,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cost_usd       REAL,               -- NULL when the model's price is unknown
    created_at     TEXT NOT NULL
);
DROP TABLE IF EXISTS ai_runs;          -- pre-multi-provider name; never held results
"""


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- HTTP cache -------------------------------------------------------
    def get_page(self, url: str, max_age_days: float) -> tuple[str, datetime] | None:
        row = self.conn.execute(
            "SELECT body, fetched_at FROM raw_pages WHERE url = ?", (url,)).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row[1])
        if datetime.now(timezone.utc) - fetched_at > timedelta(days=max_age_days):
            return None
        return row[0], fetched_at

    def save_page(self, url: str, symbol: str, basis: str, body: str,
                  fetched_at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO raw_pages VALUES (?, ?, ?, ?, ?)",
                (url, symbol, basis, body, fetched_at.isoformat()))

    # --- Normalized data --------------------------------------------------
    def save_financials(self, fin: CompanyFinancials) -> None:
        meta = fin.meta
        rows = [
            (meta.symbol, meta.basis, statement, int(year), item, None if pd.isna(value) else float(value))
            for statement, frame in fin.statements.items()
            for year, series in frame.iterrows()
            for item, value in series.items()
        ]
        with self.conn:
            self.conn.execute("DELETE FROM line_items WHERE symbol = ? AND basis = ?",
                              (meta.symbol, meta.basis))
            self.conn.executemany("INSERT INTO line_items VALUES (?, ?, ?, ?, ?, ?)", rows)
            self.conn.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?, ?)",
                (meta.symbol, meta.basis, meta.name, int(meta.is_financial),
                 meta.fetched_at.isoformat()))

    # --- Claude results -----------------------------------------------------
    def get_ai_run(self, cache_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT output_json, input_tokens, output_tokens, cost_usd FROM ai_results WHERE cache_key = ?",
            (cache_key,)).fetchone()
        if row is None:
            return None
        return {"output_json": row[0], "input_tokens": row[1], "output_tokens": row[2], "cost_usd": row[3]}

    def save_ai_run(self, cache_key: str, symbol: str, kind: str, fiscal_year: int | None,
                    model: str, prompt_version: str, output_json: str,
                    input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO ai_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cache_key, symbol, kind, fiscal_year, model, prompt_version, output_json,
                 input_tokens, output_tokens, cost_usd, datetime.now(timezone.utc).isoformat()))

    def total_ai_cost(self) -> float:
        return float(self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_results").fetchone()[0])

    def load_statements(self, symbol: str, basis: str) -> dict[str, pd.DataFrame]:
        frame = pd.read_sql_query(
            "SELECT statement, fiscal_year, item, value FROM line_items "
            "WHERE symbol = ? AND basis = ?", self.conn, params=(symbol, basis))
        return {
            statement: group.pivot(index="fiscal_year", columns="item", values="value")
            for statement, group in frame.groupby("statement")
        }
