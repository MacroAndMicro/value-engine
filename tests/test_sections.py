from conftest import FIXTURES
from value_engine.ingestion.screener import annual_report_links
from value_engine.reports.sections import build_excerpt


def _report() -> list[str]:
    """A miniature annual report: front matter, MD&A, standalone then consolidated sections."""
    pages = ["Contents\nManagement Discussion and Analysis ...... 12"] + ["Chairman's letter"] * 8
    pages += ["Management Discussion and Analysis\nIndustry outlook", "Risks and concerns",
              "Management Discussion and Analysis\nOutlook"]                                  # p10-12
    pages += ["Board's report: the statutory auditors were re-appointed at the AGM"]          # p13
    pages += ["Independent Auditor's Report\nReport on the Audit of the Standalone Financial Statements",
              "Standalone Balance Sheet (H crore) Note As at 31 March 2026",
              "Notes: contingent liabilities (standalone)"]                                   # p14-16
    pages += ["Independent Auditor's Report\nReport on the Audit of the Consolidated Financial Statements\nOpinion",
              "Key audit matters. The Consolidated Balance Sheet dealt with by this Report agrees with the books",
              "Annexure A - CARO",
              "Consolidated Balance Sheet (H crore) Note As at 31 March 2026",
              "Material accounting policies",
              "Note 30 Contingent liabilities: claims not acknowledged as debt Rs 500 crore",
              "Note 31 Related party transactions"]                                           # p17-23
    return pages


def test_consolidated_excerpt_picks_the_right_sections():
    e = build_excerpt(_report(), 2026, "consolidated")
    assert e.basis == "consolidated"
    # The mention of "Balance Sheet" inside the auditor's report must not end the block early
    assert e.sections["Independent Auditor's Report"] == [17, 18, 19]
    assert e.sections["Contingent liabilities"] == [22]          # not the standalone note on p16
    assert e.sections["Related party transactions"] == [23]
    assert e.sections["Accounting policies"] == [21]
    assert e.sections["Management Discussion & Analysis"] == [10, 11, 12]  # stops at the Board's report
    assert e.sections["Auditor appointment / resignation"] == [13]
    assert "[Page 22 | Contingent liabilities]" in e.text
    assert 1 not in e.pages  # contents page skipped


def test_standalone_excerpt_and_missing_basis_fallback():
    standalone = build_excerpt(_report(), 2026, "standalone")
    assert standalone.sections["Independent Auditor's Report"] == [14]
    assert standalone.sections["Contingent liabilities"] == [16]

    only_standalone = _report()[:16]
    fallback = build_excerpt(only_standalone, 2026, "consolidated")
    assert fallback.basis == "standalone"


def test_annual_report_links_from_real_page():
    links = annual_report_links((FIXTURES / "tcs.html").read_text(encoding="utf-8"))
    years = list(links)
    assert years[0] == 2026 and years == sorted(years, reverse=True)
    assert all(url.startswith("https://www.bseindia.com/") for url in links.values())
