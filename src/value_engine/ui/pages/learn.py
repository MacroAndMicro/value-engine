"""Learn page: the README's glossary, so definitions live in exactly one place."""
from __future__ import annotations

import re

import streamlit as st

from value_engine import config

_SECTIONS = ["Reading the result", "Glossary", "Known approximations"]  # headings to show, in order


def readme_sections(text: str, titles: list[str]) -> list[tuple[str, str]]:
    """[(heading, body)] for each heading that starts with one of `titles`. A section runs
    until the next heading of the same or higher level, so it keeps its sub-sections."""
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        match = re.match(r"^(#{2,3}) (.+)$", line)
        if not match or not any(match.group(2).startswith(t) for t in titles):
            continue
        level = len(match.group(1))
        body = []
        for next_line in lines[i + 1:]:
            heading = re.match(r"^(#{1,6}) ", next_line)
            if heading and len(heading.group(1)) <= level:
                break
            body.append(next_line)
        found.append((match.group(2), "\n".join(body).strip()))
    return found


def render() -> None:
    st.title("Learn the metrics")
    st.write("What every number on the other pages means, and why value investors care about it.")
    readme = config.ROOT_DIR / "README.md"
    if not readme.exists():
        st.warning("README.md not found.")
        return
    for heading, body in readme_sections(readme.read_text(encoding="utf-8"), _SECTIONS):
        with st.expander(heading, expanded=heading.startswith("Glossary")):
            st.markdown(body)
