"""Find the pages of an annual report that matter for forensic reading.

A 350-page Indian annual report is mostly marketing, ESG and governance boilerplate.
We send Claude only:
  * the Independent Auditor's Report (opinion, Key Audit Matters, CARO, internal controls)
  * notes on contingent liabilities, related parties and accounting-policy changes
  * Management Discussion & Analysis (MD&A)
  * auditor resignation / appointment mentions, and asset-quality pages for banks
for the same statement basis (consolidated / standalone) used by the numbers engine.
Page numbers are PDF page numbers (1-based), not the numbers printed on the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from value_engine import config

_AUDIT_START = re.compile(
    r"report on the audit of the (consolidated|standalone) (ind as )?financial statements", re.I)
# The statement's own heading, e.g. "Consolidated Balance Sheet (H crore) Note As at 31 March".
# Auditor reports also *mention* the balance sheet, so the bare phrase is not enough.
_STATEMENT_PAGE = re.compile(r"balance sheet\s*(\([^)]{0,25}\)\s*)?(note\b|as at\b)", re.I)
_MDNA = re.compile(r"management discussion and analysis", re.I)
_NEXT_SECTION = re.compile(
    r"(report on )?corporate governance( report)?|business responsibility|directors.? report|"
    r"board.?s report|independent auditor", re.I)

# (section name, pattern, max pages) searched inside the chosen basis's notes
_NOTE_SECTIONS = [
    ("Contingent liabilities", re.compile(r"contingent liabilit", re.I), 6),
    ("Related party transactions", re.compile(r"related part(y|ies)", re.I), 6),
    ("Accounting policies", re.compile(r"(material|significant) accounting polic", re.I), 3),
    ("Accounting policy changes", re.compile(
        r"change(s)? in accounting polic|new and amended standards|amendments? to ind as|restate", re.I), 3),
]
# searched anywhere in the report
_GLOBAL_SECTIONS = [
    ("Auditor appointment / resignation", re.compile(
        r"(statutory )?auditors?\b.{0,200}\b(resign|casual vacancy|re-?appoint)", re.I | re.S), 3),
]
_BANK_SECTIONS = [
    ("Asset quality (banks)", re.compile(
        r"gross npa|gross non.performing|provision coverage|casa", re.I), 6),
]
_MDNA_MAX_PAGES = 20
_AUDIT_MAX_PAGES = 14


@dataclass
class Excerpt:
    fiscal_year: int
    basis: str                                      # basis actually found in the report
    sections: dict[str, list[int]] = field(default_factory=dict)  # name -> PDF page numbers
    text: str = ""

    @property
    def pages(self) -> list[int]:
        return sorted({p for pages in self.sections.values() for p in pages})


def build_excerpt(pages: list[str], fiscal_year: int, basis: str, is_financial: bool = False) -> Excerpt:
    zones = _basis_zones(pages)
    chosen = basis if basis in zones else next(iter(zones), "whole report")
    start, end = zones.get(chosen, (0, len(pages)))
    excerpt = Excerpt(fiscal_year=fiscal_year, basis=chosen)

    audit = _auditor_report(pages, start, end) if chosen in zones else []
    if audit:
        excerpt.sections["Independent Auditor's Report"] = audit
    notes_from = (audit[-1] if audit else start)  # PDF page number == index after the block

    for name, pattern, cap in _NOTE_SECTIONS:
        hits = _matching(pages, pattern, notes_from, end, cap)
        if hits:
            excerpt.sections[name] = hits

    front_end = min(zones.values())[0] if zones else len(pages)
    mdna = _mdna_pages(pages, front_end)
    if mdna:
        excerpt.sections["Management Discussion & Analysis"] = mdna

    extra = _GLOBAL_SECTIONS + (_BANK_SECTIONS if is_financial else [])
    for name, pattern, cap in extra:
        hits = _matching(pages, pattern, 0, len(pages), cap)
        if hits:
            excerpt.sections[name] = hits

    excerpt.text = _render(pages, excerpt)
    return excerpt


def _basis_zones(pages: list[str]) -> dict[str, tuple[int, int]]:
    """{basis: (start index, end index)} - each basis runs from its auditor's report
    to the start of the other basis's report (or the end of the document)."""
    starts: dict[str, int] = {}
    for i, text in enumerate(pages):
        match = _AUDIT_START.search(text[:1500])
        if match:
            starts.setdefault(match.group(1).lower(), i)
    ordered = sorted(starts.items(), key=lambda item: item[1])
    zones = {}
    for n, (basis, start) in enumerate(ordered):
        end = ordered[n + 1][1] if n + 1 < len(ordered) else len(pages)
        zones[basis] = (start, end)
    return zones


def _auditor_report(pages: list[str], start: int, end: int) -> list[int]:
    """From the report's first page until the first financial-statement page."""
    block = []
    for i in range(start, min(end, start + _AUDIT_MAX_PAGES)):
        if i > start and _STATEMENT_PAGE.search(pages[i][:400]):
            break
        block.append(i + 1)
    return block


def _matching(pages: list[str], pattern: re.Pattern, start: int, end: int, cap: int) -> list[int]:
    return [i + 1 for i in range(start, end) if pattern.search(pages[i])][:cap]


def _mdna_pages(pages: list[str], before: int) -> list[int]:
    """The longest run of MD&A pages before the financial statements. Running headers
    often appear on alternate pages only, so gaps of up to 3 pages are bridged."""
    hits = [i for i in range(min(8, before), before) if _MDNA.search(pages[i])]  # skip the contents page
    clusters: list[list[int]] = []
    for i in hits:
        if clusters and i - clusters[-1][-1] <= 3:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    if not clusters:
        return []
    best = max(clusters, key=lambda c: c[-1] - c[0])
    if best[-1] - best[0] < 3:
        # No running header: read on from the first MD&A page to the next major section.
        last = best[0]
        while (last + 1 < min(before, best[0] + _MDNA_MAX_PAGES)
               and not _NEXT_SECTION.search(pages[last + 1][:400])):
            last += 1
    else:
        last = min(best[-1] + 1, before - 1, best[0] + _MDNA_MAX_PAGES - 1)
    return list(range(best[0] + 1, last + 2))


def _render(pages: list[str], excerpt: Excerpt) -> str:
    """Page-tagged text: '[Page 158 | Independent Auditor's Report]' + page text."""
    section_of = {}
    for name, numbers in excerpt.sections.items():
        for number in numbers:
            section_of.setdefault(number, name)
    parts, total = [], 0
    for number in excerpt.pages:
        chunk = f"[Page {number} | {section_of[number]}]\n{pages[number - 1].strip()}\n"
        if total + len(chunk) > config.EXCERPT_MAX_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)
