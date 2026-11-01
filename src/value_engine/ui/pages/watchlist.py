"""Watchlist page: screen many stocks and rank them."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from value_engine import config
from value_engine.__main__ import parse_watchlist
from value_engine.ingestion.screener import ScreenerError
from value_engine.report.summary import COLUMNS, rank_key, summary_row
from value_engine.ui import charts, state

_EXAMPLE = config.ROOT_DIR / "watchlist.example.txt"


def render() -> None:
    st.title("Watchlist screen")
    st.write("Screen several stocks at once and rank them by business quality, then price.")
    default = _EXAMPLE.read_text(encoding="utf-8") if _EXAMPLE.exists() else "TCS.NS\nINFY.NS\n"
    left, right = st.columns([3, 2])
    text = left.text_area("Tickers (one per line, # for comments)", value=default, height=200)
    upload = right.file_uploader("…or upload a .txt list", type=["txt"])
    if upload is not None:
        text = upload.getvalue().decode("utf-8", errors="replace")
    tickers = parse_watchlist(text)
    right.caption(f"{len(tickers)} ticker(s). New stocks take ~15 s each; analysed stocks are cached for an hour.")

    if st.button(f"Screen {len(tickers)} stocks", type="primary", disabled=not tickers):
        analyses, failures = [], []
        progress = st.progress(0.0)
        for i, ticker in enumerate(tickers, start=1):
            progress.progress(i / len(tickers), text=f"Analysing {ticker} ({i}/{len(tickers)})")
            try:
                analyses.append(state.run_analysis(ticker))
            except ScreenerError as exc:
                failures.append((ticker, str(exc)))
        progress.empty()
        st.session_state[state.WATCHLIST] = (sorted(analyses, key=rank_key, reverse=True), failures)

    if state.WATCHLIST in st.session_state:
        _results(*st.session_state[state.WATCHLIST])


def _results(analyses, failures) -> None:
    for ticker, error in failures:
        st.warning(f"{ticker}: {error}")
    if not analyses:
        return
    rows = pd.DataFrame([summary_row(a) for a in analyses])[COLUMNS]
    view = rows.assign(Quality=rows["quality_passed"].astype(str) + "/" + rows["quality_judged"].astype(str),
                       Valuation=rows["valuation_passed"].astype(str) + "/" + rows["valuation_judged"].astype(str))
    numeric = ["pe", "pe_median_10y", "avg_roce_pct", "implied_growth_pct", "profit_cagr_pct"]
    view = view[["ticker", "name", "screen", "Quality", "Valuation", *numeric]]
    view[numeric] = view[numeric].apply(pd.to_numeric, errors="coerce")  # "" (not meaningful) -> blank
    selection = st.dataframe(
        view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            "ticker": "Ticker", "name": "Company", "screen": "Screen",
            "pe": st.column_config.NumberColumn("P/E", format="%.1f"),
            "pe_median_10y": st.column_config.NumberColumn("10-yr median P/E", format="%.1f"),
            "avg_roce_pct": st.column_config.NumberColumn("Avg ROCE %", format="%.1f",
                                                          help="Blank for banks: ROCE doesn't apply"),
            "implied_growth_pct": st.column_config.NumberColumn("Growth price needs %", format="%.1f"),
            "profit_cagr_pct": st.column_config.NumberColumn("Past profit growth %", format="%.1f"),
        })
    st.caption("Click a row to open it on the Analyze page.")
    picked = selection.selection.rows if selection else []
    if picked:
        chosen = analyses[picked[0]]
        if st.button(f"Open {chosen.meta.name} in Analyze →", type="primary"):
            state.show(chosen, "analyze")

    st.altair_chart(charts.watchlist_scatter(rows), width="stretch")
    st.download_button("Download summary (CSV)", rows.to_csv(index=False).encode("utf-8"),
                       file_name="watchlist.csv", mime="text/csv")
