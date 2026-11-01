"""Deployment safety for the web app.

1. Hosted platforms (e.g. Streamlit Community Cloud) supply API keys as *secrets*,
   not Windows environment variables. load_secrets_into_env() copies the ones the
   providers read into os.environ, so ai/providers.py works unchanged.
2. The AI Research page spends the owner's API credit, so it is locked unless the
   visitor is on this computer (127.0.0.1) or enters AI_PAGE_PASSWORD. With no
   password configured, a hosted copy keeps the page disabled: safe by default.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

from value_engine import config

PASSWORD_NAME = "AI_PAGE_PASSWORD"
_MAX_ATTEMPTS = 5
_SECRET_NAMES = sorted({var for p in config.AI_PROVIDERS.values() for var in p["env"]}
                       | {"OPENAI_COMPATIBLE_BASE_URL", PASSWORD_NAME})


def load_secrets_into_env() -> None:
    try:
        secrets = {name: st.secrets[name] for name in _SECRET_NAMES if name in st.secrets}
    except Exception:  # no secrets.toml (normal when running locally)
        return
    for name, value in secrets.items():
        if not os.environ.get(name):
            os.environ[name] = str(value)


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def is_local_request() -> bool:
    """Visitor is on this computer. Requires BOTH the server to listen on loopback only
    (.streamlit/config.toml, which is not deployed) AND a loopback Host header, so a
    proxy that rewrites Host on a hosted site can't unlock the page."""
    server_local = (st.get_option("server.address") or "") in _LOOPBACK
    host = (st.context.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
    return server_local and host in _LOOPBACK


def ai_page_unlocked() -> bool:
    """True if this visitor may use the AI page; otherwise renders the lock and returns False."""
    if st.session_state.get("_ai_unlocked"):
        return True
    password = os.environ.get(PASSWORD_NAME, "")
    if not password:
        if is_local_request():
            return True
        st.error(f"AI research is switched off on this site: the owner hasn't set {PASSWORD_NAME} "
                 "in the app's secrets. The Analyze, Watchlist and Learn pages work without it.")
        return False

    attempts = st.session_state.get("_ai_attempts", 0)
    if attempts >= _MAX_ATTEMPTS:
        st.error("Too many wrong passwords. Reload the page to try again.")
        return False
    st.info("🔒 AI research uses the site owner's API credit, so it's password-protected. "
            "The other pages are open to everyone.")
    with st.form("ai_password"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Unlock"):
            if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
                st.session_state["_ai_unlocked"] = True
                st.rerun()
            st.session_state["_ai_attempts"] = attempts + 1
            st.error("Wrong password.")
    return False
