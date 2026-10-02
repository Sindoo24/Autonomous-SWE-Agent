"""Human review of a paused run: verdict, explanation, diff, Approve / Reject."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api.client import ApiClient, ApiError
from frontend.components import verification


def render(client: ApiClient, run_id: str, request: dict[str, Any] | None) -> None:
    if not request:
        return
    st.markdown("### Review the patch")
    verification.render(request.get("verification"))
    st.markdown(f"**Root cause:** {request.get('root_cause') or '-'}")
    st.markdown(f"**Explanation:** {request.get('explanation') or '-'}")
    if request.get("risks"):
        st.markdown("**Risks:** " + "; ".join(request["risks"]))
    st.code(client.diff(run_id), language="diff")
    reviewer = st.text_input("Your name", key="reviewer")
    c1, c2 = st.columns(2)
    if c1.button("Approve and export", type="primary"):
        client.approve(run_id, reviewer or None)
        st.rerun()
    with c2.form("reject"):
        feedback = st.text_area("What is wrong?")
        retry = st.checkbox("Let the agent try again with this feedback")
        if st.form_submit_button("Reject"):
            try:
                client.reject(run_id, feedback or None, retry, reviewer or None)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.rerun()
