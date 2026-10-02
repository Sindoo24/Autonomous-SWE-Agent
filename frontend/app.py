"""Streamlit UI for the Autonomous SWE Agent.

Submit a task, watch the run live, inspect the plan, tool calls, tests, patch and verification,
and approve or reject the patch. The UI talks to the HTTP API only; it holds no agent logic.

    streamlit run frontend/app.py            (SWE_API_URL, SWE_API_KEY from the environment)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frontend.api.client import ApiError
from frontend.pages import create_task, dashboard, experiments, task_details
from frontend.session import DEFAULT_API_URL, client

PAGES = {
    "New task": create_task.render,
    "Dashboard": dashboard.render,
    "Run": task_details.render,
    "Experiments": experiments.render,
}
ALIASES = {"Runs": "Dashboard"}  # older name of the dashboard page
DEFAULT_PAGE = "New task"


def _nav_changed() -> None:
    st.session_state["page"] = st.session_state["nav"]


def sidebar() -> str:
    with st.sidebar:
        st.title("SWE Agent")
        state = st.session_state
        page = state.get("page", DEFAULT_PAGE)
        page = ALIASES.get(page, page)
        if page not in PAGES:
            page = DEFAULT_PAGE
        # `page` is set by go() (programmatic navigation) or by the radio's callback.
        if state.get("nav") != page:
            state["nav"] = page
        choice: str = st.radio(
            "Page",
            list(PAGES),
            key="nav",
            label_visibility="collapsed",
            on_change=_nav_changed,
        )
        state["page"] = choice
        with st.expander("API connection"):
            url = st.text_input("API URL", os.environ.get("SWE_API_URL", DEFAULT_API_URL))
            key = st.text_input("API key", type="password")
            if st.button("Reconnect"):
                st.session_state.pop("client", None)
                st.session_state["api_url"], st.session_state["api_key"] = url, key
            ready = client().ready()
            for name, check in (ready.get("checks") or {}).items():
                st.caption(f"{name}: {check}")
    return choice


def router() -> None:
    try:
        PAGES[sidebar()]()
    except ApiError as exc:
        st.error(f"API error: {exc}")
    except httpx.HTTPError as exc:  # connection refused, timeout, ...
        st.error(f"Cannot reach the API ({exc}). Is `swe-agent api` running?")


st.set_page_config(page_title="SWE Agent", layout="wide")
# One routed entry page. Declaring navigation also stops Streamlit from treating the
# `frontend/pages/` package as auto-discovered multipage scripts.
st.navigation([st.Page(router, title="SWE Agent", default=True)], position="hidden").run()
