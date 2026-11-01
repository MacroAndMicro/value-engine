"""Web app entry point: page config, navigation and the shared sidebar."""
from __future__ import annotations

import streamlit as st

from value_engine.ui import security, state
from value_engine.ui.pages import analyze, learn, research, watchlist


def main() -> None:
    st.set_page_config(page_title="Value Engine", page_icon="📈", layout="wide")
    security.load_secrets_into_env()
    pages = {
        "analyze": st.Page(analyze.render, title="Analyze", icon=":material/query_stats:",
                           url_path="analyze", default=True),
        "research": st.Page(research.render, title="AI Research", icon=":material/description:",
                            url_path="research"),
        "watchlist": st.Page(watchlist.render, title="Watchlist", icon=":material/list_alt:",
                             url_path="watchlist"),
        "learn": st.Page(learn.render, title="Learn", icon=":material/school:", url_path="learn"),
    }
    st.session_state[state.PAGES] = pages
    navigation = st.navigation(list(pages.values()))

    with st.sidebar:
        st.markdown("### 📈 Value Engine")
        st.caption("Graham · Buffett · Fisher screening for NSE / BSE stocks")
        st.radio("Statements", ["Consolidated", "Standalone"], key="basis",
                 help="Consolidated includes subsidiaries (recommended). Falls back to standalone automatically.")
        st.toggle("Live prices (yfinance)", value=True, key="live_prices",
                  help="Off: use Screener's cached price, with no historical P/E.")
        if st.button("Refresh data", help="Re-download from Screener instead of using the 7-day cache"):
            st.session_state["force_refresh"] = True
            st.toast("The next analysis will download fresh data.")
        st.divider()
        st.caption("Quantitative research tool for learning. **Not investment advice.**")

    navigation.run()
