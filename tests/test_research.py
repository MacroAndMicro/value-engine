"""End-to-end Phase 2 through the Anthropic adapter with a fake SDK client: no network, no cost."""
from types import SimpleNamespace

import pandas as pd
import pytest

from conftest import make_financials
from value_engine.ai.client import AIError, Analyst
from value_engine.ai.providers import AnthropicProvider
from value_engine.ai.schemas import (AuditSummary, Category, InvestmentMemo, RedFlag, ReportFindings,
                                     Severity)
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, Status, evaluate
from value_engine.engine.valuation import compute_valuation
from value_engine.pipeline import Analysis
from value_engine.reports.fetch import AnnualReport
from value_engine.research import cap_verdict, excerpts_for, run_research
from value_engine.storage.db import Database

AUDIT_PAGE = ("Independent Auditor's Report\nReport on the Audit of the Consolidated Financial Statements\n"
              "Opinion: true and fair view. Key audit matter: revenue recognition.")
BALANCE_SHEET_PAGE = "Consolidated Balance Sheet (H crore) Note As at 31 March 2026"
NOTE_PAGE = ("Note 30 Contingent liabilities: claims not acknowledged as debt Rs 250 crore. "
             "The Company has given a corporate guarantee of Rs 900 crore for a promoter group entity.")


class FakeMessages:
    def __init__(self, outputs, stop_reason="end_turn"):
        self.outputs, self.stop_reason, self.calls = list(outputs), stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.outputs.pop(0), stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=40_000, output_tokens=8_000,
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, outputs, stop_reason="end_turn"):
        self.messages = FakeMessages(outputs, stop_reason)
        self.beta = SimpleNamespace(messages=self.messages)


def _findings(fy, auditor="B S R & Co. LLP", opinion="unmodified", flags=(), contingent=100.0):
    return ReportFindings(
        fiscal_year=fy,
        audit=AuditSummary(auditor_name=auditor, opinion=opinion, emphasis_of_matter=False,
                           going_concern_doubt=False, internal_controls_opinion="unmodified",
                           key_audit_matters=["Revenue recognition"]),
        contingent_liabilities_cr=contingent, related_party_summary="Guarantee to promoter entity",
        accounting_policy_changes=[], management_tone="Candid", bank_metrics=None,
        red_flags=list(flags), positives=["Clean opinion"])


def _memo(verdict="BUY"):
    return InvestmentMemo(verdict=verdict, one_line_thesis="t", business_quality="b", financial_strength="f",
                          valuation="v", annual_report_review="a", key_risks=["r"], what_would_change_my_mind=["w"])


REAL_FLAG = RedFlag(category=Category.RELATED_PARTY, severity=Severity.HIGH, title="Guarantee for promoter entity",
                    explanation="x", evidence_quote="has given a corporate guarantee of Rs 900 crore for a promoter",
                    page=3, amount_cr=900.0)
INVENTED_FLAG = RedFlag(category=Category.AUDITOR_CHANGE, severity=Severity.CRITICAL, title="Auditor resigned",
                        explanation="x", evidence_quote="The statutory auditor resigned citing denial of information",
                        page=3, amount_cr=None)


def _analysis(meta, cheap=True) -> Analysis:
    metrics = compute_metrics(make_financials())
    price = 150.0 if cheap else 2000.0
    prices = pd.Series((metrics["eps"] * 25).tolist(), index=metrics.index)
    valuation = compute_valuation(metrics, meta, {"price": price, "market_cap_cr": price * 10}, prices)
    return Analysis("TEST.NS", meta, metrics, valuation, evaluate(RuleContext(metrics, meta, valuation)))


def _report(fy):
    return AnnualReport("TEST", fy, "https://example.com/r.pdf", None, [AUDIT_PAGE, BALANCE_SHEET_PAGE, NOTE_PAGE])


def test_invented_evidence_is_ignored_and_verdict_is_capped(meta):
    analysis = _analysis(meta)
    db = Database(":memory:")
    fake = FakeClient([_findings(2026, flags=[REAL_FLAG, INVENTED_FLAG], contingent=1150.0), _memo("BUY")])
    reports = [_report(2026)]
    research = run_research(Analyst(db, AnthropicProvider(client=fake)), analysis, reports, excerpts_for(analysis, reports))

    [review] = research.reviews
    assert [f.verified for f in review.flags] == [True, False]
    checks = {c.name: c for c in research.checks}
    # The invented "critical" flag must not trigger AVOID; the verified high one caps at WATCHLIST
    assert checks["No serious red flags"].value == "1 high, 0 critical"
    assert research.ceiling == "WATCHLIST"
    assert research.verdict == "WATCHLIST"
    assert any("capped" in note for note in research.notes)
    assert any("discarded" in note for note in research.notes)
    # Contingent liabilities 1,150 Cr vs latest net worth from the test company
    net_worth = analysis.metrics["book_value"].iloc[-1]
    expected = 1150 / net_worth * 100
    assert checks["Contingent liabilities manageable"].value == f"{expected:.1f}%"
    # The report text went to Claude inside the excerpt tags, with page markers
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "<annual_report_excerpt>" in sent and "[Page 3 | Contingent liabilities]" in sent
    assert research.cost_usd == pytest.approx(2 * (40_000 * 4 + 8_000 * 20) / 1e6)


def test_results_are_cached_so_reruns_are_free(meta):
    analysis = _analysis(meta)
    db = Database(":memory:")
    reports = [_report(2026)]
    excerpts = excerpts_for(analysis, reports)
    run_research(Analyst(db, AnthropicProvider(client=FakeClient([_findings(2026), _memo()]))), analysis, reports, excerpts)

    second = FakeClient([])  # would raise IndexError if called
    research = run_research(Analyst(db, AnthropicProvider(client=second)), analysis, reports, excerpts)
    assert second.messages.calls == []
    assert research.cost_usd == 0
    assert research.verdict == "BUY"  # clean report + strong, cheap test company


def test_auditor_change_across_three_reports(meta):
    analysis = _analysis(meta)
    db = Database(":memory:")
    outputs = [_findings(2026, auditor="Deloitte Haskins & Sells LLP"), _findings(2025), _findings(2024),
               _memo("BUY")]
    reports = [_report(2026), _report(2025), _report(2024)]
    research = run_research(Analyst(db, AnthropicProvider(client=FakeClient(outputs))), analysis, reports,
                            excerpts_for(analysis, reports))
    checks = {c.name: c for c in research.checks}
    assert checks["Stable auditor"].status is Status.FAIL
    assert research.verdict == "WATCHLIST"


def test_qualified_opinion_forces_avoid(meta):
    analysis = _analysis(meta)
    outputs = [_findings(2026, opinion="qualified"), _memo("BUY")]
    reports = [_report(2026)]
    research = run_research(Analyst(Database(":memory:"), AnthropicProvider(client=FakeClient(outputs))), analysis, reports,
                            excerpts_for(analysis, reports))
    assert research.ceiling == "AVOID" and research.verdict == "AVOID"


def test_refusal_raises_clear_error(meta):
    analysis = _analysis(meta)
    reports = [_report(2026)]
    analyst = Analyst(Database(":memory:"), AnthropicProvider(client=FakeClient([_findings(2026)], stop_reason="refusal")))
    with pytest.raises(AIError, match="declined"):
        run_research(analyst, analysis, reports, excerpts_for(analysis, reports))


def test_cap_verdict():
    assert cap_verdict("BUY", "WATCHLIST") == "WATCHLIST"
    assert cap_verdict("AVOID", "BUY") == "AVOID"  # Claude may always be more cautious
