"""Per-browser-session state: the API client and the current page / run."""

from __future__ import annotations

import os

import streamlit as st

from frontend.api.client import ApiClient

DEFAULT_API_URL = "http://127.0.0.1:8000"


def client() -> ApiClient:
    if "client" not in st.session_state:
        url = st.session_state.get("api_url") or os.environ.get("SWE_API_URL", DEFAULT_API_URL)
        key = st.session_state.get("api_key") or os.environ.get("SWE_API_KEY") or None
        st.session_state["client"] = ApiClient(url, key)
    c: ApiClient = st.session_state["client"]
    return c


def go(page: str, run_id: str | None = None) -> None:
    st.session_state["page"] = page
    if run_id:
        st.session_state["run_id"] = run_id
