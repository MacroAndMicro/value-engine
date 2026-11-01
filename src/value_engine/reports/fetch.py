"""Download annual-report PDFs (from BSE, linked by Screener) and cache their page text.

Layout: data/reports/{SYMBOL}/{fiscal_year}.pdf and {fiscal_year}.pages.json
Text extraction takes ~40 s for a 350-page report, so it runs once per report.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

from value_engine import config


class ReportError(RuntimeError):
    pass


@dataclass
class AnnualReport:
    symbol: str
    fiscal_year: int
    url: str
    pdf_path: Path
    pages: list[str]  # page text; index 0 = PDF page 1


_HEADERS = {"User-Agent": config.USER_AGENT, "Referer": "https://www.bseindia.com/"}
_MAX_PDF_BYTES = 150_000_000


def load_report(symbol: str, fiscal_year: int, url: str,
                reports_dir: Path = config.REPORTS_DIR) -> AnnualReport:
    folder = reports_dir / symbol
    folder.mkdir(parents=True, exist_ok=True)
    pdf_path = folder / f"{fiscal_year}.pdf"
    text_path = folder / f"{fiscal_year}.pages.json"

    if not pdf_path.exists():
        _download(url, pdf_path)
    if text_path.exists():
        pages = json.loads(text_path.read_text(encoding="utf-8"))
    else:
        pages = extract_pages(pdf_path)
        text_path.write_text(json.dumps(pages), encoding="utf-8")
    if sum(len(p) for p in pages) < 10_000:
        raise ReportError(f"FY{fiscal_year} report has almost no extractable text "
                          "(probably a scanned image PDF); OCR is not supported yet.")
    return AnnualReport(symbol, fiscal_year, url, pdf_path, pages)


def extract_pages(pdf_path: Path) -> list[str]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-encoding chatter
    try:
        reader = PdfReader(pdf_path)
        return [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # corrupt or encrypted PDFs
        raise ReportError(f"Could not read {pdf_path.name}: {exc}") from exc


def _download(url: str, destination: Path) -> None:
    try:
        response = requests.get(url, headers=_HEADERS, timeout=180)
    except requests.RequestException as exc:
        raise ReportError(f"Network error downloading {url}: {exc}") from exc
    if not response.ok:
        raise ReportError(f"HTTP {response.status_code} downloading {url}")
    body = response.content
    if not body.startswith(b"%PDF"):
        raise ReportError(f"{url} did not return a PDF")
    if len(body) > _MAX_PDF_BYTES:
        raise ReportError(f"{url} is larger than {_MAX_PDF_BYTES // 1_000_000} MB")
    partial = destination.with_suffix(".part")
    partial.write_bytes(body)
    partial.replace(destination)  # never leave a half-written PDF behind
