"""Session state, cached analysis and per-action database access for the web app.

Streamlit re-runs the page script on every click, in a worker thread. SQLite
connections can't cross threads, so each action opens its own Database and closes it.
"""
from __future__ import annotations

import streamlit as st

from value_engine import config
from value_engine.ingestion.screener import ScreenerClient
from value_engine.pipeline import Analysis, analyze
from value_engine.storage.db import Database

# session_state keys
ANALYSIS = "analysis"        # the Analysis shown on the Analyze page
RESEARCH = "research"        # Research result of the last AI run
PLAN = "ai_plan"             # prepared annual reports + excerpts awaiting "Run"
WATCHLIST = "watchlist"      # (analyses, failures) of the last watchlist run
PAGES = "_pages"             # st.Page objects, for st.switch_page


def open_db() -> Database:
    return Database(config.DB_PATH)


def standalone() -> bool:
    return st.session_state.get("basis", "Consolidated") == "Standalone"


def live_prices() -> bool:
    return st.session_state.get("live_prices", True)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=64)
def _cached_analysis(ticker: str, standalone: bool, live_prices: bool, refresh: bool) -> Analysis:
    with open_db() as db:
        return analyze(ticker, ScreenerClient(db, refresh=refresh), standalone=standalone,
                       live_prices=live_prices)


def run_analysis(ticker: str) -> Analysis:
    """Analysis for one ticker, cached for an hour. Raises ScreenerError."""
    refresh = st.session_state.pop("force_refresh", False)
    if refresh:
        _cached_analysis.clear()
    return _cached_analysis(ticker.strip().upper(), standalone(), live_prices(), refresh)


def show(analysis: Analysis, page: str = "analyze") -> None:
    """Make `analysis` the current stock and jump to a page."""
    st.session_state[ANALYSIS] = analysis
    st.switch_page(st.session_state[PAGES][page])
