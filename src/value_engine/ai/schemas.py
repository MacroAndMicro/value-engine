"""Shapes Claude must return. The API's structured-output mode guarantees the JSON
matches these models, so there is no free-text parsing anywhere."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


class Category(str, Enum):
    AUDIT_OPINION = "audit_opinion"
    GOING_CONCERN = "going_concern"
    KEY_AUDIT_MATTER = "key_audit_matter"
    INTERNAL_CONTROLS = "internal_controls"
    CARO = "caro"                              # Companies (Auditor's Report) Order findings
    AUDITOR_CHANGE = "auditor_change"
    CONTINGENT_LIABILITY = "contingent_liability"
    RELATED_PARTY = "related_party"
    ACCOUNTING_POLICY = "accounting_policy"
    GOVERNANCE = "governance"
    MANAGEMENT_COMMENTARY = "management_commentary"
    ASSET_QUALITY = "asset_quality"            # banks / NBFCs
    OTHER = "other"


class RedFlag(BaseModel):
    category: Category
    severity: Severity
    title: str = Field(description="Short headline, under 12 words")
    explanation: str = Field(description="Why this matters to a long-term shareholder, in plain English")
    evidence_quote: str = Field(description="Verbatim sentence copied exactly from the excerpt, under 300 characters")
    page: int = Field(description="PDF page number from the [Page N] marker where the quote appears")
    amount_cr: float | None = Field(description="Amount involved in Rs crore, if stated; otherwise null")


class AuditSummary(BaseModel):
    auditor_name: str | None
    opinion: Literal["unmodified", "qualified", "adverse", "disclaimer", "not_found"]
    emphasis_of_matter: bool
    going_concern_doubt: bool = Field(description="True if the auditor reports a material uncertainty about going concern")
    internal_controls_opinion: Literal["unmodified", "modified", "not_found"]
    key_audit_matters: list[str] = Field(description="Titles of the Key Audit Matters")


class BankMetrics(BaseModel):
    gross_npa_pct: float | None
    net_npa_pct: float | None
    provision_coverage_pct: float | None
    casa_pct: float | None


class ReportFindings(BaseModel):
    fiscal_year: int
    audit: AuditSummary
    contingent_liabilities_cr: float | None = Field(
        description="Total claims not acknowledged as debt plus guarantees, Rs crore; null if not found")
    related_party_summary: str
    accounting_policy_changes: list[str]
    management_tone: str = Field(description="One or two sentences on how candid MD&A is about problems")
    bank_metrics: BankMetrics | None = Field(description="Only for banks/NBFCs; null otherwise")
    red_flags: list[RedFlag]
    positives: list[str] = Field(description="Up to 5 genuinely reassuring findings")


class InvestmentMemo(BaseModel):
    verdict: Literal["BUY", "WATCHLIST", "AVOID"]
    one_line_thesis: str
    business_quality: str
    financial_strength: str
    valuation: str
    annual_report_review: str
    key_risks: list[str]
    what_would_change_my_mind: list[str]
