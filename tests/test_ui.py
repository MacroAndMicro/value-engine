"""Smoke tests for the Streamlit app with a fake analysis: no network, no API calls."""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import make_financials
from value_engine.engine.metrics import compute_metrics
from value_engine.engine.rules import RuleContext, evaluate
from value_engine.engine.valuation import compute_valuation
from value_engine.pipeline import Analysis
from value_engine.ui.pages.learn import readme_sections

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _analysis(ticker: str = "TEST.NS") -> Analysis:
    from datetime import datetime, timezone

    from value_engine.models import CompanyMeta
    meta = CompanyMeta(symbol=ticker.split(".")[0], name=f"{ticker} Ltd", basis="consolidated",
                       source_url="", fetched_at=datetime.now(timezone.utc))
    metrics = compute_metrics(make_financials())
    prices = pd.Series((metrics["eps"] * 25).tolist(), index=metrics.index)
    valuation = compute_valuation(metrics, meta, {"price": 150.0, "market_cap_cr": 1500.0}, prices)
    return Analysis(ticker, meta, metrics, valuation, evaluate(RuleContext(metrics, meta, valuation)))


@pytest.fixture
def fake_analyze(monkeypatch):
    calls = []

    def fake(ticker, client, standalone=False, live_prices=True):
        calls.append(ticker)
        return _analysis(ticker)

    monkeypatch.setattr("value_engine.ui.state.analyze", fake)
    monkeypatch.setattr("value_engine.ui.state.open_db",
                        lambda: __import__("value_engine.storage.db", fromlist=["Database"]).Database(":memory:"))
    from value_engine.ui import state
    state._cached_analysis.clear()
    return calls


def test_analyze_page_renders_scorecard_charts_and_valuation(fake_analyze):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Enter a ticker" in i.value for i in at.info)

    at.text_input[0].set_value("TEST.NS")
    at.button[0].click().run()  # the form's Analyze button
    assert not at.exception, at.exception
    assert fake_analyze == ["TEST.NS"]
    labels = [m.label for m in at.metric]
    assert {"Price", "P/E", "Business quality", "Valuation", "Growth the price needs"} <= set(labels)
    scorecard = at.dataframe[0].value
    assert "Graham P/E ceiling" in set(scorecard["Rule"])

    # Reverse-DCF slider recomputes the implied growth
    before = next(m.value for m in at.metric if m.label == "Growth the price needs")
    at.slider[0].set_value(15.0).run()
    after = next(m.value for m in at.metric if m.label == "Growth the price needs")
    assert before != after


def test_watchlist_page_ranks_and_survives_failures(fake_analyze, monkeypatch):
    from value_engine.ingestion.screener import ScreenerError

    def flaky(ticker, client, standalone=False, live_prices=True):
        if ticker == "BAD.NS":
            raise ScreenerError("Not found on Screener")
        return _analysis(ticker)

    monkeypatch.setattr("value_engine.ui.state.analyze", flaky)
    at = AppTest.from_function(_run_page, args=("watchlist",), default_timeout=30).run()
    at.text_area[0].set_value("AAA.NS\nBAD.NS\nBBB.NS").run()
    next(b for b in at.button if b.label.startswith("Screen")).click().run()
    assert not at.exception, at.exception
    assert any("BAD.NS" in w.value for w in at.warning)
    table = at.dataframe[0].value
    assert list(table["ticker"]) == ["AAA.NS", "BBB.NS"]


def test_research_page_needs_an_analysis_first(monkeypatch):
    monkeypatch.setattr("value_engine.ui.security.is_local_request", lambda: True)
    monkeypatch.delenv("AI_PAGE_PASSWORD", raising=False)
    at = AppTest.from_function(_run_page, args=("research",), default_timeout=30).run()
    assert not at.exception
    assert any("Analyze a stock first" in i.value for i in at.info)


def test_hosted_ai_page_is_off_without_a_password(monkeypatch):
    monkeypatch.setattr("value_engine.ui.security.is_local_request", lambda: False)
    monkeypatch.delenv("AI_PAGE_PASSWORD", raising=False)
    at = AppTest.from_function(_run_page, args=("research",), default_timeout=30).run()
    assert any("switched off" in e.value for e in at.error)
    assert not at.info or not any("Analyze a stock first" in i.value for i in at.info)


def test_hosted_ai_page_unlocks_only_with_the_right_password(monkeypatch):
    monkeypatch.setattr("value_engine.ui.security.is_local_request", lambda: False)
    monkeypatch.setenv("AI_PAGE_PASSWORD", "correct horse battery")
    at = AppTest.from_function(_run_page, args=("research",), default_timeout=30).run()
    at.text_input[0].set_value("guess")
    at.button[0].click().run()
    assert any("Wrong password" in e.value for e in at.error)

    at.text_input[0].set_value("correct horse battery")
    at.button[0].click().run()
    assert not at.exception
    assert any("Analyze a stock first" in i.value for i in at.info)  # page content now visible


def test_secrets_are_copied_into_the_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    at = AppTest.from_function(_load_secrets, default_timeout=30)
    at.secrets["GEMINI_API_KEY"] = "from-secrets"
    at.secrets["UNRELATED"] = "ignored"
    at.run()
    assert not at.exception
    import os
    assert os.environ.get("GEMINI_API_KEY") == "from-secrets"
    assert "UNRELATED" not in os.environ
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _load_secrets() -> None:
    from value_engine.ui.security import load_secrets_into_env
    load_secrets_into_env()


def test_learn_page_shows_glossary():
    at = AppTest.from_function(_run_page, args=("learn",), default_timeout=30).run()
    assert not at.exception
    assert any("Glossary" in e.label for e in at.expander)


def test_readme_sections_keep_subsections():
    text = "# T\n## Glossary\nA\n### Valuation\nB\n## Other\nC\n### Reading the result\nD\n"
    sections = dict(readme_sections(text, ["Glossary", "Reading the result"]))
    assert sections["Glossary"] == "A\n### Valuation\nB"
    assert sections["Reading the result"] == "D"


def _run_page(name: str) -> None:
    """Render a single page as a script (AppTest runs this function's body)."""
    import streamlit as st

    from value_engine.ui import state
    from value_engine.ui.pages import analyze, learn, research, watchlist
    st.session_state[state.PAGES] = {}
    {"watchlist": watchlist, "research": research, "learn": learn, "analyze": analyze}[name].render()
