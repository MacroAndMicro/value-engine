"""Fetch 10+ years of financial statements from Screener.in's public company page.

Page layout (all selectors live here so a site change is a one-file fix):
  section#profit-loss / #balance-sheet / #cash-flow / #ratios  > table.data-table
  div#company-info[data-company-id]  -> id for the schedules (line-item breakdown) API
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from value_engine import config
from value_engine.ingestion import normalize
from value_engine.models import CompanyFinancials, CompanyMeta
from value_engine.storage.db import Database

STATEMENT_SECTIONS = {
    "profit_loss": "profit-loss",
    "balance_sheet": "balance-sheet",
    "cash_flow": "cash-flow",
    "ratios": "ratios",
}
BASES = ("consolidated", "standalone")


class ScreenerError(RuntimeError):
    pass


def company_url(symbol: str, basis: str) -> str:
    path = f"/company/{quote(symbol, safe='&')}/"
    return config.SCREENER_BASE + path + ("consolidated/" if basis == "consolidated" else "")


def schedule_url(company_id: str, parent: str, section: str, basis: str) -> str:
    params = {"parent": parent, "section": section}
    if basis == "consolidated":
        params["consolidated"] = ""
    return f"{config.SCREENER_BASE}/api/company/{company_id}/schedules/?{urlencode(params)}"


def parse_page(html: str) -> tuple[dict[str, pd.DataFrame], dict[str, str | bool | None]]:
    """Return (normalized statements, page info) from a company page."""
    soup = BeautifulSoup(html, "lxml")
    raw: dict[str, tuple[list[str], dict[str, list[str]]]] = {}
    for key, section_id in STATEMENT_SECTIONS.items():
        table = soup.select_one(f"section#{section_id} table.data-table")
        if table is not None:
            raw[key] = _read_table(table)

    statements: dict[str, pd.DataFrame] = {}
    if "profit_loss" in raw:
        fy_month = normalize.dominant_month(raw["profit_loss"][0])
        statements = {key: normalize.normalize_statement(headers, rows, fy_month)
                      for key, (headers, rows) in raw.items()}
    else:
        fy_month = None

    info = soup.select_one("#company-info")
    heading = soup.select_one("h1")
    all_labels = [label for _, rows in raw.values() for label in rows]
    page_info = {
        "name": heading.get_text(strip=True) if heading else None,
        "company_id": info.get("data-company-id") if info else None,
        "fy_month": fy_month,
        "is_financial": normalize.is_financial(all_labels),
        "ttm": _ttm_values(*raw["profit_loss"]) if "profit_loss" in raw else {},
        "snapshot": _top_ratios(soup),
    }
    return statements, page_info


def annual_report_links(html: str) -> dict[int, str]:
    """{fiscal year: PDF url} from the page's Documents > Annual reports list."""
    soup = BeautifulSoup(html, "lxml")
    links: dict[int, str] = {}
    for anchor in soup.select("section#documents .annual-reports a[href]"):
        match = re.search(r"(20\d{2})", anchor.get_text(" ", strip=True))
        if match and anchor["href"].lower().endswith(".pdf"):  # older years can be .zip archives
            links.setdefault(int(match.group(1)), anchor["href"])
    return dict(sorted(links.items(), reverse=True))


def _ttm_values(headers: list[str], rows: dict[str, list[str]]) -> dict[str, float]:
    if "TTM" not in headers:
        return {}
    i = headers.index("TTM")
    return {normalize.canonical(label): normalize.parse_number(cells[i] if i < len(cells) else None)
            for label, cells in rows.items()}


def _top_ratios(soup) -> dict[str, float]:
    snapshot = {}
    for item in soup.select("#top-ratios li"):
        name, value = item.select_one(".name"), item.select_one(".value")
        key = normalize.TOP_RATIO_LABELS.get(normalize.clean_label(name.get_text())) if name else None
        if key and value:
            snapshot[key] = normalize.parse_first_number(value.get_text(" ", strip=True))
    return snapshot


def _read_table(table) -> tuple[list[str], dict[str, list[str]]]:
    headers = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    rows: dict[str, list[str]] = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if cells:
            rows[cells[0].get_text(" ", strip=True)] = [c.get_text(strip=True) for c in cells[1:]]
    return headers, rows


class ScreenerClient:
    def __init__(self, db: Database, refresh: bool = False):
        self.db = db
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT,
                                     "Accept-Language": "en-US,en;q=0.9"})
        retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self._last_request = 0.0

    def fetch(self, symbol: str, prefer_consolidated: bool = True) -> CompanyFinancials:
        """Consolidated first; fall back to standalone if consolidated is missing or short."""
        bases = BASES if prefer_consolidated else ("standalone",)
        candidates: list[CompanyFinancials] = []
        errors: list[str] = []
        for basis in bases:
            try:
                fin = self._fetch_basis(symbol, basis)
            except ScreenerError as exc:
                errors.append(str(exc))
                continue
            if fin.valid_years("net_profit") >= config.MIN_YEARS:
                if candidates:
                    fin.meta.notes.append(
                        f"Consolidated statements had only {candidates[0].valid_years('net_profit')} "
                        "years of data; standalone statements used instead.")
                return fin
            candidates.append(fin)
        if candidates:
            best = max(candidates, key=lambda f: f.valid_years("net_profit"))
            best.meta.notes.append(
                f"Only {best.valid_years('net_profit')} years of data available.")
            return best
        raise ScreenerError("; ".join(errors) or f"No financial data found for {symbol}")

    def annual_reports(self, meta: CompanyMeta) -> dict[int, str]:
        """Annual-report PDF links listed on the (cached) company page."""
        html, _ = self._get(meta.source_url, meta.symbol, meta.basis)
        return annual_report_links(html)

    # --- internals --------------------------------------------------------
    def _get(self, url: str, symbol: str, basis: str) -> tuple[str, datetime]:
        if not self.refresh:
            cached = self.db.get_page(url, max_age_days=config.CACHE_TTL_DAYS)
            if cached:
                return cached
        wait = config.REQUEST_DELAY_SEC - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.session.get(url, timeout=config.REQUEST_TIMEOUT_SEC)
        except requests.RequestException as exc:
            raise ScreenerError(f"Network error fetching {url}: {exc}") from exc
        finally:
            self._last_request = time.monotonic()
        if resp.status_code == 404:
            raise ScreenerError(f"Not found on Screener: {url}")
        if not resp.ok:
            raise ScreenerError(f"HTTP {resp.status_code} from {url}")
        fetched_at = datetime.now(timezone.utc)
        self.db.save_page(url, symbol, basis, resp.text, fetched_at)
        return resp.text, fetched_at

    def _fetch_basis(self, symbol: str, basis: str) -> CompanyFinancials:
        url = company_url(symbol, basis)
        html, fetched_at = self._get(url, symbol, basis)
        statements, info = parse_page(html)
        if "profit_loss" not in statements or statements["profit_loss"].empty:
            raise ScreenerError(f"No annual Profit & Loss table at {url}")

        meta = CompanyMeta(symbol=symbol, name=info["name"] or symbol, basis=basis,
                           source_url=url, fetched_at=fetched_at,
                           company_id=info["company_id"], is_financial=bool(info["is_financial"]),
                           fy_month=info["fy_month"], ttm=info["ttm"], snapshot=info["snapshot"])
        if not meta.is_financial:
            self._add_capex(meta, statements)
            self._add_gross_margin(meta, statements)
            self._add_balance_sheet_detail(meta, statements)
        return CompanyFinancials(meta=meta, statements=statements)

    def _schedule(self, meta: CompanyMeta, parent: str, section: str) -> pd.DataFrame:
        """A line-item breakdown as a DataFrame; empty if unavailable."""
        if not meta.company_id:
            return pd.DataFrame()
        url = schedule_url(meta.company_id, parent, section, meta.basis)
        try:
            body, _ = self._get(url, meta.symbol, meta.basis)
            data = json.loads(body)
        except (ScreenerError, ValueError):
            return pd.DataFrame()
        return normalize.schedule_frame(data, meta.fy_month) if isinstance(data, dict) else pd.DataFrame()

    def _add_capex(self, meta: CompanyMeta, statements: dict[str, pd.DataFrame]) -> None:
        """CapEx = 'Fixed assets purchased' from the investing cash-flow breakdown.
        Fallback: change in (Net Block + CWIP) + Depreciation."""
        cash_flow = statements.get("cash_flow")
        if cash_flow is None:
            return
        purchased = self._schedule(meta, "Cash from Investing Activity", "cash-flow").get(
            "fixed_assets_purchased")
        if purchased is not None and purchased.notna().sum() >= config.MIN_YEARS:
            cash_flow["capex"] = purchased.abs().reindex(cash_flow.index)
            return

        bs = statements.get("balance_sheet", pd.DataFrame())
        pl = statements["profit_loss"]
        if "fixed_assets" not in bs or "depreciation" not in pl:
            return
        cwip = bs["cwip"].fillna(0) if "cwip" in bs else 0
        net_block_change = (bs["fixed_assets"] + cwip).diff()
        estimate = (net_block_change + pl["depreciation"].reindex(bs.index)).clip(lower=0)
        cash_flow["capex"] = estimate.reindex(cash_flow.index)
        meta.notes.append("CapEx estimated as change in (Net Block + CWIP) + Depreciation "
                          "because Screener's cash-flow breakdown was unavailable.")

    def _add_balance_sheet_detail(self, meta: CompanyMeta, statements: dict[str, pd.DataFrame]) -> None:
        """Receivables, cash, payables and lease liabilities for Graham's net-current-assets
        test and for separating bank debt from leases."""
        bs = statements.get("balance_sheet")
        if bs is None:
            return
        found = False
        for parent in ("Other Assets", "Other Liabilities", "Borrowings"):
            detail = self._schedule(meta, parent, "balance-sheet")
            for column in detail.columns:
                if column not in bs:
                    bs[column] = detail[column].reindex(bs.index)
                    found = True
        if not found:
            meta.notes.append("Balance-sheet breakdown unavailable: net current assets approximated "
                              "as Other Assets - Other Liabilities.")

    def _add_gross_margin(self, meta: CompanyMeta, statements: dict[str, pd.DataFrame]) -> None:
        """Gross margin ~= 100 - Material Cost %. Service companies report ~0% material
        cost, where gross margin is meaningless, so the report uses OPM % instead."""
        pl = statements["profit_loss"]
        material = self._schedule(meta, "Expenses", "profit-loss").get("material_cost_pct")
        if material is not None and (material.fillna(0) > 0).sum() >= config.MIN_YEARS:
            pl["gross_margin_pct"] = (100 - material).reindex(pl.index)
            meta.margin_basis = "gross"
            meta.notes.append("Gross margin approximated as 100% - Material Cost %.")
        else:
            meta.notes.append("No material-cost data (typical for services companies); "
                              "Operating Profit Margin (OPM %) used for the margin-trend rule.")
