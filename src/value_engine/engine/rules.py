"""Graham / Buffett / Fisher / earnings-quality / bank / valuation pass-fail rules.

Every rule takes a RuleContext and returns a RuleResult; nothing here fetches data
or prints. `metrics` must already be trimmed to the analysis window (config.YEARS).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from value_engine import config
from value_engine.engine.metrics import cagr, consistency, trend_slope
from value_engine.engine.valuation import Valuation
from value_engine.models import CompanyMeta


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "N/A"
    INSUFFICIENT_DATA = "NO DATA"


@dataclass
class RuleResult:
    school: str
    name: str
    status: Status
    value: str
    threshold: str
    reason: str


@dataclass
class RuleContext:
    metrics: pd.DataFrame
    meta: CompanyMeta
    valuation: Valuation | None = None


VALUATION_SCHOOLS = frozenset({"Valuation"})
_LENDER_REASON = "Not meaningful for banks/NBFCs (debt is their raw material)"


def evaluate(ctx: RuleContext) -> list[RuleResult]:
    rules = QUALITY_RULES + (BANK_RULES if ctx.meta.is_financial else []) + VALUATION_RULES
    return [rule(ctx) for rule in rules]


def score(results: list[RuleResult], valuation: bool = False) -> tuple[int, int]:
    """(passed, judged) for the business-quality rules, or the valuation rules.
    N/A and missing-data rules are excluded from the denominator."""
    judged = [r for r in results
              if r.status in (Status.PASS, Status.FAIL) and (r.school in VALUATION_SCHOOLS) == valuation]
    return sum(r.status is Status.PASS for r in judged), len(judged)


def screen(results: list[RuleResult]) -> str:
    """Provisional label combining business quality and price. Phase 2 turns this into
    a BUY / WATCHLIST / AVOID verdict once annual-report red flags are known."""
    q_pass, q_judged = score(results)
    v_pass, v_judged = score(results, valuation=True)
    if q_judged == 0:
        return "INSUFFICIENT DATA"
    quality = q_pass / q_judged
    cheap = v_judged > 0 and v_pass / v_judged >= config.VALUATION_PASS_FRACTION
    if quality >= config.QUALITY_STRONG_FRACTION:
        return "QUALITY + VALUE" if cheap else "QUALITY, EXPENSIVE"
    if quality >= config.QUALITY_MIXED_FRACTION:
        return "MIXED"
    return "WEAK"


# --- helpers ---------------------------------------------------------------
def _valid(series: pd.Series) -> pd.Series:
    return series.replace([np.inf, -np.inf], np.nan).dropna()


def _required_years(available: int) -> int:
    return math.ceil(config.GOOD_YEARS_FRACTION * available)


def _insufficient(school: str, name: str, threshold: str, years: int,
                  reason: str | None = None) -> RuleResult:
    return RuleResult(school, name, Status.INSUFFICIENT_DATA, f"{years} yrs", threshold,
                      reason or f"Need at least {config.MIN_YEARS} years of data")


def _not_applicable(school: str, name: str, threshold: str, reason: str = _LENDER_REASON) -> RuleResult:
    return RuleResult(school, name, Status.NOT_APPLICABLE, "-", threshold, reason)


def _status(ok: bool) -> Status:
    return Status.PASS if ok else Status.FAIL


def _fmt_x(value: float) -> str:
    return "no debt" if math.isinf(value) else f"{value:.1f}x"


# --- Graham: margin of safety -------------------------------------------------
def graham_no_losses(ctx: RuleContext) -> RuleResult:
    name, threshold = "No loss-making years", f"<= {config.MAX_LOSS_YEARS} loss years"
    profits = ctx.metrics["net_profit"].dropna()
    if len(profits) < config.MIN_YEARS:
        return _insufficient("Graham", name, threshold, len(profits))
    loss_years = [str(y) for y, v in profits.items() if v < 0]
    reason = f"Losses in {', '.join(loss_years)}" if loss_years else "Profitable every year"
    return RuleResult("Graham", name, _status(len(loss_years) <= config.MAX_LOSS_YEARS),
                      f"{len(loss_years)} of {len(profits)} yrs", threshold, reason)


def graham_debt_vs_nca(ctx: RuleContext) -> RuleResult:
    name, threshold = "Total debt <= Net current assets", "Debt <= NCA (latest year)"
    if ctx.meta.is_financial:
        return _not_applicable("Graham", name, threshold)
    m = ctx.metrics
    both = m[["borrowings", "net_current_assets"]].dropna()
    if both.empty:
        return _insufficient("Graham", name, threshold, 0)
    year = both.index[-1]
    debt, nca = both.loc[year, "borrowings"], both.loc[year, "net_current_assets"]
    leases = m.loc[year, "lease_liabilities"]
    lease_note = f"; Rs {leases:,.0f} Cr of the debt is leases" if leases > 0 else ""
    return RuleResult("Graham", name, _status(debt <= nca),
                      f"{debt:,.0f} vs {nca:,.0f}", threshold,
                      f"FY{year}: debt is {'covered' if debt <= nca else 'NOT covered'} by NCA{lease_note}")


def graham_interest_coverage(ctx: RuleContext) -> RuleResult:
    name, threshold = "Strong interest coverage", f">= {config.INTEREST_COVERAGE_MIN:.0f}x every year"
    if ctx.meta.is_financial:
        return _not_applicable("Graham", name, threshold)
    icr = ctx.metrics["interest_coverage"].dropna()
    if len(icr) < config.MIN_YEARS:
        return _insufficient("Graham", name, threshold, len(icr))
    weak = [str(y) for y, v in icr.items() if v < config.INTEREST_COVERAGE_MIN]
    reason = f"Weak in {', '.join(weak)}" if weak else f"Weakest year FY{icr.idxmin()}"
    return RuleResult("Graham", name, _status(not weak), f"min {_fmt_x(icr.min())}", threshold, reason)


# --- Buffett: moat & cash -----------------------------------------------------
def buffett_avg_roce(ctx: RuleContext) -> RuleResult:
    name, threshold = "High average ROCE", f"10-yr avg > {config.ROCE_MIN_PCT:.0f}%"
    if ctx.meta.is_financial:
        return _not_applicable("Buffett", name, threshold)
    stats = consistency(ctx.metrics["roce_pct"], config.ROCE_MIN_PCT)
    if stats["years"] < config.MIN_YEARS:
        return _insufficient("Buffett", name, threshold, stats["years"])
    return RuleResult("Buffett", name, _status(stats["mean"] > config.ROCE_MIN_PCT),
                      f"{stats['mean']:.1f}%", threshold,
                      f"Volatility (CV) {stats['cv']:.2f} - {'steady' if stats['cv'] < 0.25 else 'volatile'}")


def buffett_roce_consistency(ctx: RuleContext) -> RuleResult:
    name = "Consistent ROCE"
    if ctx.meta.is_financial:
        return _not_applicable("Buffett", name, f"> {config.ROCE_MIN_PCT:.0f}% in most years")
    stats = consistency(ctx.metrics["roce_pct"], config.ROCE_MIN_PCT)
    years = stats["years"]
    threshold = f"> {config.ROCE_MIN_PCT:.0f}% in >= {_required_years(years)} of {years} yrs"
    if years < config.MIN_YEARS:
        return _insufficient("Buffett", name, threshold, years)
    return RuleResult("Buffett", name, _status(stats["years_above"] >= _required_years(years)),
                      f"{stats['years_above']} of {years} yrs", threshold,
                      f"{stats['pct_years_above']:.0f}% of years cleared the bar")


def buffett_avg_roe(ctx: RuleContext) -> RuleResult:
    name, threshold = "High average ROE", f"10-yr avg > {config.ROE_MIN_PCT:.0f}%"
    stats = consistency(ctx.metrics["roe_pct"], config.ROE_MIN_PCT)
    if stats["years"] < config.MIN_YEARS:
        return _insufficient("Buffett", name, threshold, stats["years"])
    return RuleResult("Buffett", name, _status(stats["mean"] > config.ROE_MIN_PCT),
                      f"{stats['mean']:.1f}%", threshold,
                      f"Above {config.ROE_MIN_PCT:.0f}% in {stats['years_above']} of {stats['years']} yrs")


def buffett_fcf_positive(ctx: RuleContext) -> RuleResult:
    name = "Positive free cash flow"
    if ctx.meta.is_financial:
        return _not_applicable("Buffett", name, "FCF > 0 in most years")
    fcf = _valid(ctx.metrics["fcf"])
    threshold = f"> 0 in >= {_required_years(len(fcf))} of {len(fcf)} yrs"
    if len(fcf) < config.MIN_YEARS:
        return _insufficient("Buffett", name, threshold, len(fcf))
    positive = int((fcf > 0).sum())
    negative = [str(y) for y, v in fcf.items() if v <= 0]
    reason = f"Negative in {', '.join(negative)}" if negative else "Positive every year"
    return RuleResult("Buffett", name, _status(positive >= _required_years(len(fcf))),
                      f"{positive} of {len(fcf)} yrs", threshold, reason)


def buffett_fcf_growth(ctx: RuleContext) -> RuleResult:
    n = config.FCF_TREND_WINDOW
    name, threshold = "Growing free cash flow", f"avg last {n} yrs > avg first {n} yrs"
    if ctx.meta.is_financial:
        return _not_applicable("Buffett", name, threshold)
    fcf = _valid(ctx.metrics["fcf"])
    if len(fcf) < config.MIN_YEARS:
        return _insufficient("Buffett", name, threshold, len(fcf))
    early, late = fcf.iloc[:n].mean(), fcf.iloc[-n:].mean()
    growth = f"{(late / early - 1) * 100:+.0f}%" if early > 0 else "n/m"
    return RuleResult("Buffett", name, _status(late > early),
                      f"{early:,.0f} -> {late:,.0f}", threshold, f"Change {growth}")


def buffett_low_leverage(ctx: RuleContext) -> RuleResult:
    name, threshold = "Low debt-to-equity", f"< {config.DEBT_TO_EQUITY_MAX} (latest year)"
    if ctx.meta.is_financial:
        return _not_applicable("Buffett", name, threshold)
    de = _valid(ctx.metrics["debt_to_equity"])
    if de.empty:
        return _insufficient("Buffett", name, threshold, 0)
    year, latest = de.index[-1], de.iloc[-1]
    return RuleResult("Buffett", name, _status(0 <= latest < config.DEBT_TO_EQUITY_MAX),
                      f"{latest:.2f}", threshold,
                      f"FY{year}; 10-yr peak {de.max():.2f}" + (" (negative equity!)" if latest < 0 else ""))


# --- Fisher: quality & growth -------------------------------------------------
def fisher_margin_trend(ctx: RuleContext) -> RuleResult:
    name = f"Stable/expanding {ctx.meta.margin_basis} margin"
    threshold = f"trend >= {config.MARGIN_SLOPE_MIN} pp/yr"
    if ctx.meta.is_financial:
        return _not_applicable("Fisher", name, threshold)
    margin = ctx.metrics["margin_pct"].dropna()
    if len(margin) < config.MIN_YEARS:
        return _insufficient("Fisher", name, threshold, len(margin))
    slope = trend_slope(margin)
    return RuleResult("Fisher", name, _status(slope >= config.MARGIN_SLOPE_MIN),
                      f"{slope:+.2f} pp/yr", threshold,
                      f"{margin.iloc[0]:.0f}% (FY{margin.index[0]}) -> {margin.iloc[-1]:.0f}% (FY{margin.index[-1]})")


def fisher_cash_conversion(ctx: RuleContext) -> RuleResult:
    name, threshold = "Profits backed by cash", f"cumulative CFO/NP >= {config.CFO_TO_NP_MIN}"
    if ctx.meta.is_financial:
        return _not_applicable("Fisher", name, threshold)
    both = ctx.metrics[["cfo", "net_profit"]].dropna()
    if len(both) < config.MIN_YEARS:
        return _insufficient("Fisher", name, threshold, len(both))
    total_np = both["net_profit"].sum()
    if total_np <= 0:
        return RuleResult("Fisher", name, Status.FAIL, "n/m", threshold, "Cumulative net profit is not positive")
    ratio = both["cfo"].sum() / total_np
    return RuleResult("Fisher", name, _status(ratio >= config.CFO_TO_NP_MIN), f"{ratio:.2f}", threshold,
                      f"Rs {both['cfo'].sum():,.0f} Cr cash vs Rs {total_np:,.0f} Cr profit")


def fisher_divergence(ctx: RuleContext) -> RuleResult:
    name, threshold = "Few profit/cash divergences", f"<= {config.MAX_DIVERGENCE_YEARS} years"
    if ctx.meta.is_financial:
        return _not_applicable("Fisher", name, threshold)
    m = ctx.metrics
    if len(m[["cfo", "net_profit"]].dropna()) < config.MIN_YEARS:
        return _insufficient("Fisher", name, threshold, len(m[["cfo", "net_profit"]].dropna()))
    flagged = [str(y) for y in m.index if m.loc[y, "divergence"]]
    reason = f"Profit up, CFO down in {', '.join(flagged)}" if flagged else "No divergence years"
    return RuleResult("Fisher", name, _status(len(flagged) <= config.MAX_DIVERGENCE_YEARS),
                      f"{len(flagged)} yrs", threshold, reason)


# --- Earnings quality: are the reported profits what they seem? ----------------
_EQ = "Earnings quality"


def eq_debtor_days(ctx: RuleContext) -> RuleResult:
    name = "Receivables under control"
    threshold = (f"latest <= {config.DEBTOR_DAYS_MAX_RATIO}x 10-yr median "
                 f"(or <= {config.DEBTOR_DAYS_SAFE} days)")
    if ctx.meta.is_financial:
        return _not_applicable(_EQ, name, threshold)
    days = ctx.metrics["debtor_days"].dropna()
    if len(days) < config.MIN_YEARS:
        return _insufficient(_EQ, name, threshold, len(days))
    median, latest = days.median(), days.iloc[-1]
    ok = latest <= config.DEBTOR_DAYS_SAFE or latest <= config.DEBTOR_DAYS_MAX_RATIO * median
    return RuleResult(_EQ, name, _status(ok), f"{latest:.0f} vs {median:.0f} days", threshold,
                      "Customers paying as usual" if ok else "Customers taking longer to pay - sales may be pushed")


def eq_other_income(ctx: RuleContext) -> RuleResult:
    name = "Profit from core business"
    threshold = f"other income <= {config.OTHER_INCOME_MAX_PCT:.0f}% of PBT (3-yr avg)"
    if ctx.meta.is_financial:
        return _not_applicable(_EQ, name, threshold, "Fee income is core business for lenders")
    share = _valid(ctx.metrics["other_income_share_pct"]).tail(3)
    if len(share) < 3:
        return _insufficient(_EQ, name, threshold, len(share), "Need 3 recent years")
    avg = share.mean()
    return RuleResult(_EQ, name, _status(avg <= config.OTHER_INCOME_MAX_PCT), f"{avg:.0f}%", threshold,
                      "Interest, dividends, one-off gains" if avg > config.OTHER_INCOME_MAX_PCT
                      else "Mostly operating profit")


def eq_tax_rate(ctx: RuleContext) -> RuleResult:
    name, threshold = "Normal tax rate", f"median last 5 yrs >= {config.TAX_RATE_MIN_PCT:.0f}%"
    tax = ctx.metrics["tax_pct"].dropna().tail(5)
    if len(tax) < 3:
        return _insufficient(_EQ, name, threshold, len(tax), "Need 3 recent years")
    median = tax.median()
    return RuleResult(_EQ, name, _status(median >= config.TAX_RATE_MIN_PCT), f"{median:.0f}%", threshold,
                      "Profits look fully taxed" if median >= config.TAX_RATE_MIN_PCT
                      else "Low tax: check for exemptions ending or profits that aren't taxable")


def eq_per_share_growth(ctx: RuleContext) -> RuleResult:
    name = "Per-share growth keeps up"
    threshold = f"EPS CAGR >= profit CAGR - {config.EPS_LAG_MAX_PP:.0f}pp"
    m = ctx.metrics
    if m["eps"].notna().sum() < config.MIN_YEARS:
        return _insufficient(_EQ, name, threshold, int(m["eps"].notna().sum()))
    eps_growth, np_growth = cagr(m["eps"]) * 100, cagr(m["net_profit"]) * 100
    if math.isnan(eps_growth) or math.isnan(np_growth):
        return _insufficient(_EQ, name, threshold, int(m["eps"].notna().sum()), "Loss year at start or end")
    ok = eps_growth >= np_growth - config.EPS_LAG_MAX_PP
    return RuleResult(_EQ, name, _status(ok), f"{eps_growth:.1f}% vs {np_growth:.1f}%", threshold,
                      "Shareholders get the full growth" if ok
                      else "New shares or minority holders are absorbing the growth")


# --- Banks / NBFCs ---------------------------------------------------------------
def bank_avg_roa(ctx: RuleContext) -> RuleResult:
    name, threshold = "High return on assets", f"10-yr avg >= {config.BANK_ROA_MIN_PCT:.1f}%"
    stats = consistency(ctx.metrics["roa_pct"], config.BANK_ROA_MIN_PCT)
    if stats["years"] < config.MIN_YEARS:
        return _insufficient("Bank", name, threshold, stats["years"])
    return RuleResult("Bank", name, _status(stats["mean"] >= config.BANK_ROA_MIN_PCT),
                      f"{stats['mean']:.2f}%", threshold,
                      f"Above {config.BANK_ROA_MIN_PCT:.1f}% in {stats['years_above']} of {stats['years']} yrs")


def bank_deposit_growth(ctx: RuleContext) -> RuleResult:
    name, threshold = "Growing deposit franchise", f"deposit CAGR >= {config.DEPOSIT_CAGR_MIN_PCT:.0f}%"
    deposits = ctx.metrics["deposits"].dropna()
    if deposits.empty:
        return _not_applicable("Bank", name, threshold, "No deposits reported (NBFC)")
    if len(deposits) < config.MIN_YEARS:
        return _insufficient("Bank", name, threshold, len(deposits))
    growth = cagr(deposits) * 100
    return RuleResult("Bank", name, _status(growth >= config.DEPOSIT_CAGR_MIN_PCT), f"{growth:.1f}%",
                      threshold, "Cheap, sticky funding is a bank's moat")


def bank_nii_trend(ctx: RuleContext) -> RuleResult:
    name, threshold = "Stable lending spread", f"NII/assets trend >= {config.NII_SLOPE_MIN} pp/yr"
    nii = ctx.metrics["nii_to_assets_pct"].dropna()
    if len(nii) < config.MIN_YEARS:
        return _insufficient("Bank", name, threshold, len(nii))
    slope = trend_slope(nii)
    return RuleResult("Bank", name, _status(slope >= config.NII_SLOPE_MIN), f"{slope:+.2f} pp/yr", threshold,
                      f"{nii.iloc[0]:.2f}% -> {nii.iloc[-1]:.2f}% (net interest income / assets)")


# --- Valuation: margin of safety on price ----------------------------------------
_VAL = "Valuation"
_NO_PRICE = "No price available"


def valuation_graham_pe(ctx: RuleContext) -> RuleResult:
    name, threshold = "Graham P/E ceiling", f"P/E <= {config.GRAHAM_PE_MAX:.0f}"
    v = ctx.valuation
    if v is None:
        return _insufficient(_VAL, name, threshold, 0, _NO_PRICE)
    if math.isnan(v.pe):
        return RuleResult(_VAL, name, Status.FAIL, "n/m", threshold, "No positive trailing earnings")
    return RuleResult(_VAL, name, _status(v.pe <= config.GRAHAM_PE_MAX), f"{v.pe:.1f}", threshold,
                      f"Earnings yield {v.earnings_yield_pct:.1f}%")


def valuation_vs_history(ctx: RuleContext) -> RuleResult:
    # Banks are valued on book value; everyone else on earnings.
    use_pb = ctx.meta.is_financial
    label = "P/B" if use_pb else "P/E"
    name, threshold = "Cheaper than own history", f"{label} <= 10-yr median {label}"
    v = ctx.valuation
    if v is None:
        return _insufficient(_VAL, name, threshold, 0, _NO_PRICE)
    current, median = (v.pb, v.pb_median) if use_pb else (v.pe, v.pe_median)
    history = v.pb_history if use_pb else v.pe_history
    years = int(history.notna().sum())
    if years < config.MIN_YEARS or math.isnan(median):
        return _insufficient(_VAL, name, threshold, years, "Historical prices unavailable")
    if math.isnan(current):
        return RuleResult(_VAL, name, Status.FAIL, "n/m", threshold, f"Current {label} not meaningful")
    discount = (current / median - 1) * 100
    return RuleResult(_VAL, name, _status(current <= median), f"{current:.1f} vs {median:.1f}", threshold,
                      f"{abs(discount):.0f}% {'below' if discount <= 0 else 'above'} its usual {label}")


def valuation_reverse_dcf(ctx: RuleContext) -> RuleResult:
    name, threshold = "Price implies achievable growth", "implied FCF growth <= past 10-yr profit growth"
    v = ctx.valuation
    if ctx.meta.is_financial:
        return _not_applicable(_VAL, name, threshold, "Bank cash flows can't be discounted like this")
    if v is None:
        return _insufficient(_VAL, name, threshold, 0, _NO_PRICE)
    if not v.fcf_base_cr > 0:
        return RuleResult(_VAL, name, Status.FAIL, "n/m", threshold,
                          "Recent free cash flow is negative: the price rests on hope, not cash")
    if math.isnan(v.implied_growth_pct):
        return RuleResult(_VAL, name, Status.FAIL, "> 100%", threshold, "Price needs implausible growth")
    if math.isnan(v.profit_cagr_pct):
        return _insufficient(_VAL, name, threshold, 0, "Past growth not measurable (loss year)")
    ok = v.implied_growth_pct <= v.profit_cagr_pct
    return RuleResult(_VAL, name, _status(ok),
                      f"{v.implied_growth_pct:.1f}% vs {v.profit_cagr_pct:.1f}%", threshold,
                      f"Market assumes {v.implied_growth_pct:.1f}%/yr for {config.DCF_YEARS} yrs "
                      f"at a {config.DISCOUNT_RATE:.0%} discount rate")


QUALITY_RULES = [
    graham_no_losses, graham_debt_vs_nca, graham_interest_coverage,
    buffett_avg_roce, buffett_roce_consistency, buffett_avg_roe,
    buffett_fcf_positive, buffett_fcf_growth, buffett_low_leverage,
    fisher_margin_trend, fisher_cash_conversion, fisher_divergence,
    eq_debtor_days, eq_other_income, eq_tax_rate, eq_per_share_growth,
]
BANK_RULES = [bank_avg_roa, bank_deposit_growth, bank_nii_trend]
VALUATION_RULES = [valuation_graham_pe, valuation_vs_history, valuation_reverse_dcf]
