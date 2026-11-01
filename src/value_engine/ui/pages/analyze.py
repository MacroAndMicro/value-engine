"""Analyze page: one stock's 10-year fundamentals, scorecard and valuation."""
from __future__ import annotations

import math

import streamlit as st

from value_engine import config
from value_engine.engine.rules import score, screen
from value_engine.engine.valuation import reverse_dcf
from value_engine.ingestion.screener import ScreenerError
from value_engine.pipeline import Analysis
from value_engine.ui import charts, state
from value_engine.ui.formatting import (SCREEN_BADGE, crores, fundamentals_frame, number, pct, rules_frame,
                                        rupees)


def render() -> None:
    st.title("Analyze a stock")
    current: Analysis | None = st.session_state.get(state.ANALYSIS)
    with st.form("analyze", border=False):
        col, button = st.columns([4, 1], vertical_alignment="bottom")
        ticker = col.text_input("NSE / BSE ticker", value=current.ticker if current else "TCS.NS",
                                help="NSE tickers end in .NS (e.g. INFY.NS), BSE codes in .BO (e.g. 500325.BO)")
        submitted = button.form_submit_button("Analyze", type="primary", width="stretch")
    if submitted and ticker.strip():
        try:
            with st.spinner(f"Fetching 10 years of data for {ticker.strip().upper()}... "
                            "(first time ~15 s, then cached)"):
                current = state.run_analysis(ticker)
            st.session_state[state.ANALYSIS] = current
        except ScreenerError as exc:
            st.error(str(exc))
            return
    if current is None:
        st.info("Enter a ticker and press **Analyze**. Try TCS.NS, ITC.NS, RELIANCE.NS or HDFCBANK.NS.")
        return

    _header(current)
    scorecard, trends, valuation, data = st.tabs(["Scorecard", "10-year charts", "Valuation", "Data"])
    with scorecard:
        _scorecard(current)
    with trends:
        _charts(current)
    with valuation:
        _valuation(current)
    with data:
        _data(current)


def _header(a: Analysis) -> None:
    meta, v = a.meta, a.valuation
    q_pass, q_judged = score(a.results)
    v_pass, v_judged = score(a.results, valuation=True)
    label = screen(a.results)

    title, badge = st.columns([3, 1], vertical_alignment="center")
    title.subheader(f"{meta.name}  ·  {meta.symbol}")
    with badge:
        st.badge(label, color=SCREEN_BADGE[label])
    st.caption(f"{meta.basis.title()} statements · {meta.units} · data fetched {meta.fetched_at:%d %b %Y}"
               + (f" · price from {v.price_source}" if v else "")
               + (" · **Bank/NBFC**: bank rules applied" if meta.is_financial else ""))

    cols = st.columns(5)
    cols[0].metric("Price", rupees(v.price if v else None, 2))
    cols[1].metric("Market cap", crores(v.market_cap_cr if v else None))
    if v and not math.isnan(v.pe):
        delta = None if math.isnan(v.pe_median) else f"{v.pe - v.pe_median:+.1f} vs 10-yr median"
        cols[2].metric("P/E", number(v.pe), delta, delta_color="inverse",
                       help="Price ÷ trailing-12-month earnings per share. Lower than its own history = cheaper than usual.")
    else:
        cols[2].metric("P/E", "-")
    cols[3].metric("Business quality", f"{q_pass}/{q_judged}",
                   help="Graham, Buffett, Fisher, earnings-quality (and bank) rules passed. N/A rules excluded.")
    cols[4].metric("Valuation", f"{v_pass}/{v_judged}", help="Price-based rules passed.")

    if st.button("Review annual reports with AI →", help="Phase 2: an LLM reads the annual report for red flags"):
        state.show(a, "research")


def _scorecard(a: Analysis) -> None:
    frame = rules_frame(a.results)
    st.dataframe(frame, hide_index=True, width="stretch",
                 column_config={"Why": st.column_config.TextColumn(width="large")})
    st.caption("✅ pass · ❌ fail · ➖ not applicable to this kind of company · ⚠️ not enough data. "
               "See the **Learn** page for what each rule means.")


def _charts(a: Analysis) -> None:
    m, v = a.metrics, a.valuation
    if a.meta.is_financial:
        left, right = st.columns(2)
        left.altair_chart(charts.grouped_bars(m, {"Revenue": "sales", "Net profit": "net_profit"},
                                              "Revenue and profit"), width="stretch")
        right.altair_chart(charts.lines(m, {"ROA %": "roa_pct"}, "Return on assets", "%",
                                        config.BANK_ROA_MIN_PCT, f"{config.BANK_ROA_MIN_PCT:.0f}% bar"),
                           width="stretch")
        left.altair_chart(charts.lines(m, {"NII / assets %": "nii_to_assets_pct"}, "Lending spread", "%"),
                          width="stretch")
        right.altair_chart(charts.grouped_bars(m, {"Deposits": "deposits"}, "Deposits"), width="stretch")
    else:
        left, right = st.columns(2)
        left.altair_chart(charts.grouped_bars(m, {"Sales": "sales"}, "Sales"), width="stretch")
        right.altair_chart(charts.profit_vs_cash(m), width="stretch")
        left.altair_chart(charts.fcf_bars(m), width="stretch")
        right.altair_chart(charts.returns_chart(m), width="stretch")
        margin = "Gross margin %" if a.meta.margin_basis == "gross" else "Operating margin %"
        left.altair_chart(charts.lines(m, {margin: "margin_pct"}, "Margin trend", "%"), width="stretch")
        right.altair_chart(charts.lines(m, {"Debtor days": "debtor_days"}, "Days to collect from customers",
                                        "days"), width="stretch")
    if v is not None and v.pe_history.notna().any():
        st.altair_chart(charts.pe_chart(v.pe_history, v.pe_median), width="stretch")
    st.caption("Hover any bar or point for exact values. The same numbers are in the **Data** tab.")


def _valuation(a: Analysis) -> None:
    v = a.valuation
    if v is None:
        st.info("No price available, so valuation can't be computed. Turn on live prices in the sidebar.")
        return
    cols = st.columns(4)
    cols[0].metric("Earnings yield", pct(v.earnings_yield_pct),
                   help="1 ÷ P/E: profit per ₹100 invested. Compare with a fixed deposit rate.")
    cols[1].metric("P/B", number(v.pb, 2), None if math.isnan(v.pb_median) else f"median {v.pb_median:.2f}",
                   delta_color="off", help="Price ÷ book value per share. Banks are judged on this.")
    gap = (v.price / v.graham_number - 1) * 100 if v.graham_number > 0 else math.nan
    cols[2].metric("Graham Number", rupees(v.graham_number),
                   None if math.isnan(gap) else f"price {gap:+.0f}% vs it", delta_color="inverse",
                   help="√(22.5 × EPS × book value per share): the most Graham would pay. Harsh on high-ROE firms.")
    cols[3].metric("FCF yield", pct(v.fcf_yield_pct), help="3-yr average free cash flow ÷ market cap.")

    st.divider()
    st.subheader("What growth is the price assuming?")
    if a.meta.is_financial:
        st.info("A reverse DCF doesn't suit banks: their cash flows are deposits and loans. Use P/B vs its history.")
        return
    if not v.fcf_base_cr > 0:
        st.warning("Recent free cash flow is negative, so the price can't be explained by cash flows at all.")
        return
    st.write("A **reverse DCF** works backwards from today's price: what yearly free-cash-flow growth "
             "for the next 10 years would make the business worth what the market charges? "
             "Move the sliders to see how the answer depends on your assumptions.")
    left, right = st.columns(2)
    rate = left.slider("Your required return (discount rate)", 8.0, 16.0, config.DISCOUNT_RATE * 100, 0.5,
                       format="%.1f%%", help="Indian large caps: ~11-13% is a common cost of equity.")
    terminal = right.slider("Growth after year 10 (forever)", 3.0, 7.0, config.TERMINAL_GROWTH * 100, 0.5,
                            format="%.1f%%", help="Roughly long-run nominal GDP growth or a little below.")
    implied = reverse_dcf(v.enterprise_value_cr, v.fcf_base_cr, rate / 100, terminal / 100) * 100
    out = st.columns(3)
    out[0].metric("Growth the price needs", "> 100%" if math.isnan(implied) else pct(implied))
    out[1].metric("Profit growth delivered (10 yrs)", pct(v.profit_cagr_pct))
    out[2].metric("Sales growth delivered (10 yrs)", pct(v.sales_cagr_pct))
    if not math.isnan(implied) and not math.isnan(v.profit_cagr_pct):
        if implied <= v.profit_cagr_pct:
            st.success(f"The price needs {implied:.1f}%/yr, no more than the {v.profit_cagr_pct:.1f}%/yr the company "
                       "has delivered. You aren't paying for heroic growth.")
        else:
            st.warning(f"The price needs {implied:.1f}%/yr, more than the {v.profit_cagr_pct:.1f}%/yr delivered. "
                       "You'd be paying for growth the company hasn't shown yet.")
    st.caption(f"Starting point: 3-yr average FCF {crores(v.fcf_base_cr)}; enterprise value "
               f"{crores(v.enterprise_value_cr)} (market cap + debt − cash).")


def _data(a: Analysis) -> None:
    st.dataframe(fundamentals_frame(a), width="stretch")
    st.download_button("Download 10-year metrics (CSV)", a.metrics.to_csv().encode("utf-8"),
                       file_name=f"{a.meta.symbol}-metrics.csv", mime="text/csv")
    if a.meta.notes:
        st.markdown("**Notes on the data**")
        for note in a.meta.notes:
            st.markdown(f"- {note}")
