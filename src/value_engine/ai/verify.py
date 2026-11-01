"""Check every red flag's evidence_quote against the report text Claude was given.

A language model can misremember or invent a quote. The rule here: a red flag only
counts toward the verdict if its quote is actually on the page it cites (or a nearby
excerpt page, in which case the page number is corrected).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from value_engine.ai.schemas import RedFlag

_MIN_MATCH_RATIO = 0.85  # share of the quote that must appear contiguously


@dataclass
class VerifiedFlag:
    flag: RedFlag
    verified: bool
    page: int            # corrected page when the quote was found elsewhere


def normalize(text: str) -> str:
    """Lowercase letters and digits only. Removes differences that PDF extraction
    introduces (curly quotes, ligatures, hyphenation, line breaks, odd spaces)."""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def quote_on_page(quote: str, page_text: str) -> bool:
    needle, haystack = normalize(quote), normalize(page_text)
    if len(needle) < 12:  # too short to prove anything
        return False
    if needle in haystack:
        return True
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack))
    return match.size / len(needle) >= _MIN_MATCH_RATIO


def verify_flags(flags: list[RedFlag], pages: list[str], excerpt_pages: list[int]) -> list[VerifiedFlag]:
    """pages: full report text (index 0 = PDF page 1); excerpt_pages: pages Claude saw."""
    results = []
    for flag in flags:
        candidates = [flag.page] + [p for p in excerpt_pages if p != flag.page]
        found = next((p for p in candidates
                      if 1 <= p <= len(pages) and p in excerpt_pages and quote_on_page(flag.evidence_quote, pages[p - 1])),
                     None)
        results.append(VerifiedFlag(flag, verified=found is not None, page=found or flag.page))
    return results
