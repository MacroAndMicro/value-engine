"""Phase 2: read annual reports with an LLM, verify the evidence, and write a memo.

Principle: the math decides, the AI explains.
  1. The LLM extracts findings from page-tagged report excerpts (structured output).
  2. Code verifies every quoted piece of evidence against the real page text.
  3. Code turns findings into hard checks and sets a verdict *ceiling*.
  4. The LLM writes the memo; its verdict is capped at the ceiling.
Works the same with any provider in ai/providers.py.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from value_engine import config
from value_engine.ai.client import AIResult, Analyst, estimate_cost
from value_engine.ai.prompts import FORENSIC_SYSTEM, MEMO_SYSTEM
from value_engine.ai.schemas import SEVERITY_ORDER, InvestmentMemo, ReportFindings, Severity
from value_engine.ai.verify import VerifiedFlag, verify_flags
from value_engine.engine.rules import RuleResult, Status, score, screen
from value_engine.pipeline import Analysis
from value_engine.ingestion.screener import ScreenerClient
from value_engine.reports.fetch import AnnualReport, ReportError, load_report
from value_engine.reports.sections import Excerpt, build_excerpt

VERDICTS = ["AVOID", "WATCHLIST", "BUY"]  # least to most favourable
_FINDINGS_OUTPUT_TOKENS = 8000            # incl. thinking; used for the cost estimate
_MEMO_OUTPUT_TOKENS = 6000
_MEMO_INPUT_CHARS = 30_000
_SCHOOL = "Annual report"


@dataclass
class ReportReview:
    report: AnnualReport
    excerpt: Excerpt
    findings: ReportFindings
    flags: list[VerifiedFlag]
    ai: AIResult

    @property
    def verified_flags(self) -> list[VerifiedFlag]:
        return [f for f in self.flags if f.verified]


@dataclass
class Research:
    analysis: Analysis
    reviews: list[ReportReview]
    checks: list[RuleResult]
    cross_year: list[str]
    ceiling: str
    ceiling_reasons: list[str]
    memo: InvestmentMemo
    verdict: str
    memo_ai: AIResult
    model_label: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float | None:
        """Cost of the API calls made this run; None if any call's price is unknown."""
        fresh = [r.ai for r in self.reviews if not r.ai.cached] + ([] if self.memo_ai.cached else [self.memo_ai])
        if any(ai.cost_usd is None for ai in fresh):
            return None
        return sum(ai.cost_usd for ai in fresh)


# --- planning -----------------------------------------------------------------
def prepare_reports(analysis: Analysis, screener: ScreenerClient, count: int,
                    on_progress: Callable[[int], None] | None = None) -> tuple[list[AnnualReport], list[Excerpt]]:
    """Download (once) the latest `count` annual reports and cut them down to excerpts.
    on_progress(fiscal_year) is called before each report, for progress displays."""
    links = screener.annual_reports(analysis.meta)
    if not links:
        raise ReportError("No annual reports are linked on Screener for this company.")
    reports = []
    for fiscal_year, url in list(links.items())[:count]:
        if on_progress:
            on_progress(fiscal_year)
        reports.append(load_report(analysis.meta.symbol, fiscal_year, url))
    return reports, excerpts_for(analysis, reports)


def excerpts_for(analysis: Analysis, reports: list[AnnualReport]) -> list[Excerpt]:
    return [build_excerpt(r.pages, r.fiscal_year, analysis.meta.basis, analysis.meta.is_financial)
            for r in reports]


def estimate_research_cost(excerpts: list[Excerpt], model: str) -> float | None:
    """None when the model's price is not configured in config.AI_PRICES."""
    parts = [estimate_cost(model, len(e.text), _FINDINGS_OUTPUT_TOKENS) for e in excerpts]
    parts.append(estimate_cost(model, _MEMO_INPUT_CHARS, _MEMO_OUTPUT_TOKENS))
    return None if any(p is None for p in parts) else sum(parts)


# --- step 1 + 2: read and verify ------------------------------------------------
def review_report(analyst: Analyst, analysis: Analysis, report: AnnualReport,
                  excerpt: Excerpt) -> ReportReview:
    meta = analysis.meta
    lender = " The company is a bank/NBFC: fill bank_metrics." if meta.is_financial else ""
    content = (f"Company: {meta.name} ({meta.symbol}). Annual report for FY{report.fiscal_year}. "
               f"Statement basis: {excerpt.basis}.{lender}\n\n"
               f"<annual_report_excerpt>\n{excerpt.text}\n</annual_report_excerpt>\n\n"
               "Analyse this excerpt and return your findings.")
    ai = analyst.run(kind="findings", symbol=meta.symbol, fiscal_year=report.fiscal_year,
                     system=FORENSIC_SYSTEM, user_content=content, schema=ReportFindings)
    findings: ReportFindings = ai.output
    flags = verify_flags(findings.red_flags, report.pages, excerpt.pages)
    return ReportReview(report, excerpt, findings, flags, ai)


# --- step 3: hard checks and the verdict ceiling ----------------------------------
def annual_report_checks(analysis: Analysis, reviews: list[ReportReview]) -> list[RuleResult]:
    latest = reviews[0].findings  # reviews are newest first
    meta = analysis.meta
    checks: list[RuleResult] = []

    opinions = {r.findings.fiscal_year: r.findings.audit.opinion for r in reviews}
    modified = {fy: op for fy, op in opinions.items() if op in ("qualified", "adverse", "disclaimer")}
    if modified:
        checks.append(_check("Clean audit opinion", Status.FAIL, ", ".join(f"FY{y}: {o}" for y, o in modified.items()),
                             "unmodified opinion", "The auditor did not fully sign off on the accounts"))
    elif latest.audit.opinion == "not_found":
        checks.append(_check("Clean audit opinion", Status.INSUFFICIENT_DATA, "-", "unmodified opinion",
                             "Auditor's opinion not found in the excerpt"))
    else:
        checks.append(_check("Clean audit opinion", Status.PASS, "unmodified", "unmodified opinion",
                             f"Auditor: {latest.audit.auditor_name or 'n/a'}"))

    doubt = [r.findings.fiscal_year for r in reviews if r.findings.audit.going_concern_doubt]
    checks.append(_check("No going-concern doubt", Status.FAIL if doubt else Status.PASS,
                         ", ".join(f"FY{y}" for y in doubt) or "none", "no material uncertainty",
                         "Auditor doubts the company can keep operating" if doubt else "No doubt raised"))

    icfr = latest.audit.internal_controls_opinion
    checks.append(_check("Internal controls effective",
                         {"unmodified": Status.PASS, "modified": Status.FAIL}.get(icfr, Status.INSUFFICIENT_DATA),
                         icfr, "unmodified ICFR opinion",
                         "Weak controls make errors and fraud more likely" if icfr == "modified"
                         else "Controls over financial reporting judged adequate" if icfr == "unmodified"
                         else "ICFR opinion not found"))

    name = "Contingent liabilities manageable"
    threshold = f"<= {config.CONTINGENT_LIABILITY_MAX_PCT:.0f}% of net worth"
    net_worth = analysis.metrics["book_value"].dropna()
    if meta.is_financial:
        checks.append(_check(name, Status.NOT_APPLICABLE, "-", threshold,
                             "Banks' off-balance-sheet items (guarantees, forex contracts) are business as usual"))
    elif latest.contingent_liabilities_cr is None or net_worth.empty:
        checks.append(_check(name, Status.INSUFFICIENT_DATA, "-", threshold, "Total not found in the report"))
    else:
        pct = latest.contingent_liabilities_cr / net_worth.iloc[-1] * 100
        checks.append(_check(name, _pass(pct <= config.CONTINGENT_LIABILITY_MAX_PCT), f"{pct:.1f}%", threshold,
                             f"Rs {latest.contingent_liabilities_cr:,.0f} Cr of claims/guarantees vs "
                             f"Rs {net_worth.iloc[-1]:,.0f} Cr net worth"))

    serious = [f for r in reviews for f in r.verified_flags
               if SEVERITY_ORDER[f.flag.severity] >= SEVERITY_ORDER[Severity.HIGH]]
    critical = sum(f.flag.severity is Severity.CRITICAL for f in serious)
    checks.append(_check("No serious red flags", _pass(not serious),
                         f"{len(serious) - critical} high, {critical} critical", "0 verified high/critical",
                         "; ".join(f.flag.title for f in serious[:3]) or "None found with verified evidence"))

    auditors = [(r.findings.fiscal_year, r.findings.audit.auditor_name) for r in reviews if r.findings.audit.auditor_name]
    if len(auditors) < 2:
        checks.append(_check("Stable auditor", Status.NOT_APPLICABLE, "-", "same auditor across years",
                             "Needs 2+ reports (use --reports 3)"))
    else:
        names = {_firm(n) for _, n in auditors}
        checks.append(_check("Stable auditor", _pass(len(names) == 1), f"{len(names)} firm(s)",
                             "same auditor across years",
                             "Same audit firm every year" if len(names) == 1
                             else "Auditor changed: " + " -> ".join(f"FY{y} {n}" for y, n in reversed(auditors))))

    if meta.is_financial:
        bank = latest.bank_metrics
        gnpa = bank.gross_npa_pct if bank else None
        casa = bank.casa_pct if bank else None
        checks.append(_check("Low bad loans (Gross NPA)",
                             Status.INSUFFICIENT_DATA if gnpa is None else _pass(gnpa <= config.BANK_GNPA_MAX_PCT),
                             "-" if gnpa is None else f"{gnpa:.2f}%", f"<= {config.BANK_GNPA_MAX_PCT:.0f}%",
                             "Share of loans not being repaid"))
        checks.append(_check("Cheap deposits (CASA)",
                             Status.INSUFFICIENT_DATA if casa is None else _pass(casa >= config.BANK_CASA_MIN_PCT),
                             "-" if casa is None else f"{casa:.1f}%", f">= {config.BANK_CASA_MIN_PCT:.0f}%",
                             "Current + savings deposits: the cheapest funding a bank has"))
    return checks


def cross_year_observations(reviews: list[ReportReview]) -> list[str]:
    """Deterministic comparisons between years (needs --reports 3)."""
    notes = []
    years = sorted(reviews, key=lambda r: r.findings.fiscal_year)
    amounts = [(r.findings.fiscal_year, r.findings.contingent_liabilities_cr) for r in years
               if r.findings.contingent_liabilities_cr]
    if len(amounts) >= 2 and amounts[0][1] > 0:
        change = (amounts[-1][1] / amounts[0][1] - 1) * 100
        notes.append(f"Contingent liabilities moved {change:+.0f}% from FY{amounts[0][0]} "
                     f"(Rs {amounts[0][1]:,.0f} Cr) to FY{amounts[-1][0]} (Rs {amounts[-1][1]:,.0f} Cr).")
    for previous, current in zip(years, years[1:]):
        old = set(map(str.lower, previous.findings.audit.key_audit_matters))
        new = [k for k in current.findings.audit.key_audit_matters if k.lower() not in old]
        if new:
            notes.append(f"New Key Audit Matters in FY{current.findings.fiscal_year}: {'; '.join(new)}.")
        if current.findings.accounting_policy_changes:
            notes.append(f"FY{current.findings.fiscal_year} accounting-policy changes: "
                         f"{'; '.join(current.findings.accounting_policy_changes)}.")
    return notes


def verdict_ceiling(analysis: Analysis, checks: list[RuleResult],
                    reviews: list[ReportReview]) -> tuple[str, list[str]]:
    failed = {c.name for c in checks if c.status is Status.FAIL}
    verified = [f for r in reviews for f in r.verified_flags]
    label = screen(analysis.results)

    avoid = []
    if "Clean audit opinion" in failed:
        avoid.append("the auditor issued a modified opinion")
    if "No going-concern doubt" in failed:
        avoid.append("the auditor doubts the company's ability to continue as a going concern")
    if any(f.flag.severity is Severity.CRITICAL for f in verified):
        avoid.append("a critical red flag with verified evidence")
    if label == "WEAK":
        avoid.append("the business fails most quality rules (screen: WEAK)")
    if avoid:
        return "AVOID", avoid

    watch = []
    if label != "QUALITY + VALUE":
        q, qj = score(analysis.results)
        v, vj = score(analysis.results, valuation=True)
        watch.append(f"the numbers screen is '{label}' (quality {q}/{qj}, valuation {v}/{vj})")
    if any(f.flag.severity is Severity.HIGH for f in verified):
        watch.append("high-severity red flags with verified evidence")
    for name in ("Internal controls effective", "Contingent liabilities manageable", "Stable auditor",
                 "Low bad loans (Gross NPA)", "Cheap deposits (CASA)"):
        if name in failed:
            watch.append(f"failed check: {name}")
    if watch:
        return "WATCHLIST", watch
    return "BUY", ["strong business, reasonable price and a clean annual-report review"]


def cap_verdict(proposed: str, ceiling: str) -> str:
    return min(proposed, ceiling, key=VERDICTS.index)


# --- step 4: the memo -------------------------------------------------------------
def write_memo(analyst: Analyst, analysis: Analysis, reviews: list[ReportReview],
               checks: list[RuleResult], cross_year: list[str], ceiling: str,
               reasons: list[str]) -> AIResult:
    payload = memo_input(analysis, reviews, checks, cross_year, ceiling, reasons)
    content = f"<research>\n{json.dumps(payload, indent=1)}\n</research>\n\nWrite the investment memo."
    return analyst.run(kind="memo", symbol=analysis.meta.symbol, fiscal_year=None,
                       system=MEMO_SYSTEM, user_content=content, schema=InvestmentMemo)


def memo_input(analysis: Analysis, reviews: list[ReportReview], checks: list[RuleResult],
               cross_year: list[str], ceiling: str, reasons: list[str]) -> dict:
    meta, m, v = analysis.meta, analysis.metrics, analysis.valuation
    columns = (["sales", "net_profit", "roa_pct", "roe_pct", "nii_to_assets_pct", "deposits"] if meta.is_financial
               else ["sales", "net_profit", "cfo", "fcf", "roce_pct", "roe_pct", "debt_to_equity", "margin_pct"])
    q, qj = score(analysis.results)
    vp, vj = score(analysis.results, valuation=True)
    return _clean({
        "company": {"name": meta.name, "symbol": meta.symbol, "statements": meta.basis,
                    "is_bank_or_nbfc": meta.is_financial, "units": meta.units,
                    "margin_basis": meta.margin_basis},
        "ten_year_fundamentals": {str(y): {c: m.loc[y, c] for c in columns} for y in m.index},
        "rules": [{"school": r.school, "rule": r.name, "status": r.status.value, "value": r.value,
                   "reason": r.reason} for r in analysis.results],
        "scores": {"business_quality": f"{q}/{qj}", "valuation": f"{vp}/{vj}", "screen": screen(analysis.results)},
        "valuation": None if v is None else {
            "price": v.price, "market_cap_cr": v.market_cap_cr, "pe": v.pe, "pe_10y_median": v.pe_median,
            "pb": v.pb, "pb_10y_median": v.pb_median, "graham_number": v.graham_number,
            "fcf_yield_pct": v.fcf_yield_pct, "implied_fcf_growth_pct": v.implied_growth_pct,
            "profit_cagr_10y_pct": v.profit_cagr_pct, "sales_cagr_10y_pct": v.sales_cagr_pct,
        },
        "annual_reports": [{
            "fiscal_year": r.findings.fiscal_year,
            "audit": r.findings.audit.model_dump(),
            "contingent_liabilities_cr": r.findings.contingent_liabilities_cr,
            "related_party_summary": r.findings.related_party_summary,
            "accounting_policy_changes": r.findings.accounting_policy_changes,
            "management_tone": r.findings.management_tone,
            "bank_metrics": r.findings.bank_metrics.model_dump() if r.findings.bank_metrics else None,
            "positives": r.findings.positives,
            "verified_red_flags": [{"severity": f.flag.severity.value, "category": f.flag.category.value,
                                    "title": f.flag.title, "explanation": f.flag.explanation,
                                    "page": f.page, "amount_cr": f.flag.amount_cr} for f in r.verified_flags],
            "unverified_red_flags_discarded": len(r.flags) - len(r.verified_flags),
        } for r in reviews],
        "annual_report_checks": [{"check": c.name, "status": c.status.value, "value": c.value,
                                  "reason": c.reason} for c in checks],
        "cross_year_observations": cross_year,
        "verdict_ceiling": ceiling,
        "ceiling_reasons": reasons,
    })


def run_research(analyst: Analyst, analysis: Analysis, reports: list[AnnualReport],
                 excerpts: list[Excerpt]) -> Research:
    pairs = sorted(zip(reports, excerpts), key=lambda pair: pair[0].fiscal_year, reverse=True)
    reviews = [review_report(analyst, analysis, report, excerpt) for report, excerpt in pairs]
    checks = annual_report_checks(analysis, reviews)
    cross_year = cross_year_observations(reviews)
    ceiling, reasons = verdict_ceiling(analysis, checks, reviews)
    memo_ai = write_memo(analyst, analysis, reviews, checks, cross_year, ceiling, reasons)
    memo: InvestmentMemo = memo_ai.output
    verdict = cap_verdict(memo.verdict, ceiling)
    notes = []
    if verdict != memo.verdict:
        notes.append(f"The model proposed {memo.verdict}; capped to {verdict} by the hard rules.")
    for review in reviews:
        dropped = len(review.flags) - len(review.verified_flags)
        if dropped:
            notes.append(f"FY{review.findings.fiscal_year}: {dropped} red flag(s) discarded because their "
                         "quoted evidence was not found in the report.")
        if review.excerpt.basis != analysis.meta.basis:
            notes.append(f"FY{review.findings.fiscal_year}: {analysis.meta.basis} statements not found in the "
                         f"report; {review.excerpt.basis} section used.")
    return Research(analysis, reviews, checks, cross_year, ceiling, reasons, memo, verdict, memo_ai,
                    model_label=analyst.label, notes=notes)


# --- helpers ------------------------------------------------------------------
def _check(name: str, status: Status, value: str, threshold: str, reason: str) -> RuleResult:
    return RuleResult(_SCHOOL, name, status, value, threshold, reason)


def _pass(ok: bool) -> Status:
    return Status.PASS if ok else Status.FAIL


def _firm(name: str) -> str:
    """'B S R & Co. LLP' and 'BSR & Co LLP, Chartered Accountants' -> same firm key."""
    name = re.sub(r"chartered accountants?|llp|& ?co\.?|\bco\b", "", name.lower())
    return re.sub(r"[^a-z]", "", name)


def _clean(value):
    """JSON-safe: NaN/inf -> None, floats rounded, numpy scalars -> Python."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 2)
    return value
