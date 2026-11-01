"""AI Research page: prepare the annual reports, show the cost, then run the LLM review."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from value_engine import config
from value_engine.ai.client import AIError, Analyst
from value_engine.ai.providers import PROVIDERS, make_provider
from value_engine.ingestion.screener import ScreenerClient, ScreenerError
from value_engine.pipeline import Analysis
from value_engine.report.research_view import memo_markdown, save_memo
from value_engine.reports.fetch import ReportError
from value_engine.research import Research, estimate_research_cost, prepare_reports, run_research
from value_engine.ui import security, state
from value_engine.ui.formatting import VERDICT_BOX, rules_frame

_SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}


def render() -> None:
    st.title("AI annual-report review")
    if not security.ai_page_unlocked():
        return
    a: Analysis | None = st.session_state.get(state.ANALYSIS)
    if a is None:
        st.info("Analyze a stock first, then come back here.")
        if st.button("Go to Analyze"):
            st.switch_page(st.session_state[state.PAGES]["analyze"])
        return
    st.caption(f"{a.meta.name} · {a.meta.symbol} · {a.meta.basis} statements")

    with st.expander("Which providers have an API key?", expanded=False):
        st.dataframe(_key_status(), hide_index=True, width="stretch")
        st.caption("Each provider needs its own key in its own environment variable. Keys are never shown here.")

    left, mid, right = st.columns([2, 2, 1])
    names = list(PROVIDERS)
    provider_name = left.selectbox("Provider", names, index=names.index(config.AI_PROVIDER),
                                   format_func=lambda n: f"{config.AI_PROVIDERS[n]['label']} ({n})")
    model = mid.text_input("Model", value=config.AI_PROVIDERS[provider_name]["model"] or "",
                           key=f"model_{provider_name}", placeholder="required for openai-compatible")
    depth = right.radio("Reports", ["Latest", "Last 3"], help="3 reports add year-over-year checks "
                        "(auditor changes, new audit concerns) at ~3x the cost.")
    count = 3 if depth == "Last 3" else 1

    if st.button("1 · Prepare annual reports", help="Downloads the PDFs (free) and picks the pages to send"):
        _prepare(a, provider_name, model, count)

    plan = st.session_state.get(state.PLAN)
    if plan and plan["symbol"] == a.meta.symbol and plan["count"] == count:
        _show_plan(plan, provider_name, model)

    research: Research | None = st.session_state.get(state.RESEARCH)
    if research and research.analysis.meta.symbol == a.meta.symbol:
        _show_research(research)


def _key_status() -> pd.DataFrame:
    rows = []
    for name, settings in config.AI_PROVIDERS.items():
        try:
            make_provider(name, settings["model"] or "placeholder").check_ready()
            status = "✅ key found"
        except AIError:
            status = "❌ not set"
        rows.append({"Provider": settings["label"], "--provider": name,
                     "Environment variable": " or ".join(settings["env"]),
                     "Default model": settings["model"] or "(choose)", "Status": status})
    return pd.DataFrame(rows)


def _prepare(a: Analysis, provider_name: str, model: str, count: int) -> None:
    try:
        with st.status("Preparing annual reports...", expanded=True) as status:
            with state.open_db() as db:
                reports, excerpts = prepare_reports(
                    a, ScreenerClient(db), count,
                    on_progress=lambda fy: status.write(f"Downloading and reading the FY{fy} report "
                                                        "(first time takes about a minute)..."))
            status.update(label=f"{len(reports)} report(s) ready", state="complete", expanded=False)
    except (ReportError, ScreenerError) as exc:
        st.error(str(exc))
        return
    st.session_state[state.PLAN] = {"symbol": a.meta.symbol, "count": count,
                                    "reports": reports, "excerpts": excerpts}


def _show_plan(plan: dict, provider_name: str, model: str) -> None:
    excerpts = plan["excerpts"]
    st.dataframe(pd.DataFrame([{"Report": f"FY{e.fiscal_year}", "Statements": e.basis, "Pages sent": len(e.pages),
                                "~Tokens": len(e.text) // 4, "Sections": ", ".join(e.sections)} for e in excerpts]),
                 hide_index=True, width="stretch")
    estimate = estimate_research_cost(excerpts, model) if model else None
    cost = "cost unknown" if estimate is None else f"~${estimate:.2f}"
    st.caption("Results are cached: re-running the same reports with the same model is free.")
    if st.button(f"2 · Run analysis with {config.AI_PROVIDERS[provider_name]['label']} ({cost})", type="primary"):
        _run(plan, provider_name, model)


def _run(plan: dict, provider_name: str, model: str) -> None:
    a: Analysis = st.session_state[state.ANALYSIS]
    try:
        provider = make_provider(provider_name, model or None)
        provider.check_ready()
        with st.status(f"{provider.label} is reading the report(s) and writing the memo... "
                       "(can take a few minutes)", expanded=False) as status, state.open_db() as db:
            analyst = Analyst(db, provider)
            research = run_research(analyst, a, plan["reports"], plan["excerpts"])
            status.update(label="Done", state="complete")
    except AIError as exc:
        st.error(str(exc))
        return
    save_memo(research, config.MEMOS_DIR)
    st.session_state[state.RESEARCH] = research


def _show_research(r: Research) -> None:
    st.divider()
    getattr(st, VERDICT_BOX[r.verdict])(f"**Verdict: {r.verdict}**  ·  {r.memo.one_line_thesis}")
    with st.expander(f"Why the ceiling is {r.ceiling}"):
        for reason in r.ceiling_reasons:
            st.markdown(f"- {reason}")
    cost = r.cost_usd
    st.caption(f"Model: {r.model_label} · cost of this run: "
               + ("free (cached)" if cost == 0 else "unknown" if cost is None else f"${cost:.2f}"))

    st.subheader("Annual-report checks")
    st.dataframe(rules_frame(r.checks).drop(columns=["School"]), hide_index=True, width="stretch")

    st.subheader("Red flags")
    flags = [(rev.findings.fiscal_year, f) for rev in r.reviews for f in rev.flags]
    if not flags:
        st.success("No red flags found.")
    for year, f in flags:
        icon = _SEVERITY_ICON[f.flag.severity.value]
        evidence = "✅ verified" if f.verified else "❌ quote NOT found in report - ignored"
        with st.expander(f"{icon} FY{year} · {f.flag.severity.value.upper()} · {f.flag.title}  ({evidence})"):
            st.write(f.flag.explanation)
            st.markdown(f"> {f.flag.evidence_quote}")
            st.caption(f"{f.flag.category.value.replace('_', ' ')} · PDF page {f.page}"
                       + (f" · ₹{f.flag.amount_cr:,.0f} Cr" if f.flag.amount_cr else ""))
    if r.cross_year:
        st.subheader("Year over year")
        for note in r.cross_year:
            st.markdown(f"- {note}")

    st.subheader("Investment memo")
    st.markdown(memo_markdown(r, heading=False))
    st.download_button("Download memo (.md)", memo_markdown(r).encode("utf-8"),
                       file_name=f"{r.analysis.meta.symbol}-memo.md", mime="text/markdown")
    for note in r.notes:
        st.caption(note)
